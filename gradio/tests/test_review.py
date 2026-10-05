"""Review imports, complete exports, semantic comparisons and UI callbacks."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from annotation.dataset import load_dataset
from annotation.export import export_archive, export_documents
from annotation.session import new_session, commit
from annotation.summary import changes_between, build_summary
from annotation.workflow import Workflow
from crop.crop import crop_document, image_resize
import app

TITLE = 'Nguyên văn chữ Hán Nôm'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.images = [self.root / (code + '.png') for code in ('1', '2', '3')]
        for image in self.images:
            Image.new('RGB', (100, 100), 'white').save(image)
        self.documents = [dict(
            image=image.name,
            bounding_boxes={'1': dict(bbox=[10, 10, 20, 20], status='intact'),
                            '2': dict(bbox=[10, 30, 20, 40], status='damaged', unknown=True)},
            annotations={'1': '永', '2': '寺'},
            image_resize=image_resize([100, 100], [100, 100]),
            crop=crop_document(image.name, [0, 0, 100, 100], [100, 100])['crop'],
        ) for image in self.images]
        self.content = [dict(image=image.name, inscription_code=image.stem,
                             content={TITLE: '永寺', 'Toát yếu': None, 'Tên bia': 'Test'})
                        for image in self.images]
        self.zip = self.root / 'annotations.zip'
        self.write_zip()
        self.baseline = load_dataset(self.zip, self.images)
        self.engine = Workflow(SimpleNamespace(content_titles=[TITLE], annotation_title=TITLE))
        self.addCleanup(self.engine._preview_cache.cleanup)

    def write_zip(self, annotations=None, content=None, mismatches=None, suspicious=None):
        with zipfile.ZipFile(self.zip, 'w') as archive:
            for filename, data in {
                'text_annotations.json': self.documents if annotations is None else annotations,
                'inscription_content.json': self.content if content is None else content,
                'source_mismatches.json': mismatches or [],
                'suspicious_details.json': suspicious or {},
            }.items():
                archive.writestr(filename, json.dumps(data, ensure_ascii=False))

    def active(self, ctx=None, index=0):
        ctx = deepcopy(ctx) if ctx else new_session(self.baseline)
        image = self.images[index]
        bundle = ctx['committed'].get(image.name, ctx['baseline'][image.name])
        ctx['active'] = self.engine.open_image(image, bundle)
        return ctx

    def fixed_state(self, ctx):
        state = deepcopy(ctx['active'])
        state['mode'] = 'fix'
        state['current_step'] = 2
        state['workflow']['content_verified'] = False
        return state

    def complete(self, state):
        while state['current_step'] != 7:
            state = self.engine.apply(state, 'next')
        return state

    def test_finish_preserves_original_and_exports_unreviewed_records(self):
        ctx = commit(self.active(), [TITLE], finish=True)
        docs = export_documents(ctx)
        self.assertEqual(docs['review_text_annotations.json'], self.documents)
        self.assertEqual(docs['review_inscription_content.json'], self.content)
        self.assertEqual(docs['review_summary.json']['counts'],
                         dict(total=3, accepted=1, fixed=0, unreviewed=2))
        self.assertEqual(self.baseline, ctx['baseline'])

    def test_unchanged_fix_is_accepted_and_preserves_null_metadata(self):
        ctx = self.active()
        ctx['active'] = self.complete(self.fixed_state(ctx))
        ctx = commit(ctx, [TITLE])
        self.assertEqual(ctx['committed']['1.png'], self.baseline['1.png'])
        self.assertEqual(build_summary(ctx)['images'][0]['result'], 'accepted')

    def test_content_and_character_corrections_commit_together(self):
        ctx = self.active()
        state = self.fixed_state(ctx)
        state = self.engine.apply(state, 'field', dict(path=['content', TITLE], value='永楽'))
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        docs = export_documents(ctx)
        self.assertEqual(docs['review_inscription_content.json'][0]['content'][TITLE], '永楽')
        self.assertIsNone(docs['review_inscription_content.json'][0]['content']['Toát yếu'])
        self.assertEqual(docs['review_text_annotations.json'][0]['annotations'], {'1': '永', '2': '楽'})
        self.assertTrue(all('order' not in box for box in docs['review_text_annotations.json'][0]['bounding_boxes'].values()))
        changes = docs['review_summary.json']['images'][0]['changes']
        self.assertTrue(any(c['type'] == 'content_changed' and c['before'] == '永寺' for c in changes))
        self.assertTrue(any(c['type'] == 'character_changed' and c['original_box_id'] == '2' for c in changes))

    def test_unfinished_edit_does_not_replace_committed_content(self):
        ctx = commit(self.active(), [TITLE], finish=True)
        ctx = self.active(ctx)
        state = self.fixed_state(ctx)
        ctx['active'] = self.engine.apply(state, 'field', dict(path=['content', TITLE], value='永楽'))
        docs = export_documents(ctx)
        self.assertEqual(docs['review_inscription_content.json'], self.content)
        self.assertEqual(build_summary(ctx)['images'][0]['result'], 'accepted')

    def test_reordering_tracks_regions_without_false_character_corrections(self):
        result = deepcopy(self.baseline['1.png'])
        result['document']['bounding_boxes'] = {'1': result['document']['bounding_boxes']['2'],
                                                '2': result['document']['bounding_boxes']['1']}
        result['document']['annotations'] = {'1': '寺', '2': '永'}
        result['origins'] = {'1': '2', '2': '1'}
        changes = changes_between(self.baseline['1.png'], result)
        self.assertEqual([c['type'] for c in changes], ['reading_order_changed', 'reading_order_changed'])

    def test_reset_after_committed_content_fix_restores_baseline(self):
        ctx = self.active()
        state = self.engine.apply(self.fixed_state(ctx), 'field',
                                  dict(path=['content', 'Tên bia'], value='Corrected'))
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        ctx['notes']['1.png'] = {'content': 'Keep this note'}
        # Reset creates a fresh draft from the immutable imported bundle.
        ctx['active'] = self.engine.open_image(self.images[0], ctx['baseline']['1.png'])
        ctx['active'] = self.complete(self.fixed_state(ctx))
        ctx = commit(ctx, [TITLE])
        self.assertEqual(ctx['committed']['1.png'], self.baseline['1.png'])
        self.assertEqual(build_summary(ctx)['images'][0]['notes']['content'], 'Keep this note')

    def test_missing_content_is_not_invented_when_only_geometry_changes(self):
        self.write_zip(content=[])
        baseline = load_dataset(self.zip, self.images)
        ctx = new_session(baseline)
        ctx['active'] = self.engine.open_image(self.images[0], baseline['1.png'])
        state = self.fixed_state(ctx)
        state = self.engine.apply(state, 'next')
        uid = state['region_uid_by_box_id']['1']
        state = self.engine.apply(state, 'update', dict(id=uid, bbox=[9, 10, 20, 20]))
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        self.assertIsNone(ctx['committed']['1.png']['content'])
        self.assertEqual(export_documents(ctx)['review_inscription_content.json'], [])

    def test_suspicious_removal_updates_both_files(self):
        self.documents[0]['issue_type'] = ['suspicious_content']
        self.write_zip(suspicious={'1': dict(issue_type='suspicious_content', box_ids=[1], note='Content may be incorrect.')})
        baseline = load_dataset(self.zip, self.images)
        ctx = new_session(baseline)
        ctx['active'] = self.engine.open_image(self.images[0], baseline['1.png'])
        state = self.fixed_state(ctx)
        state['suspicious_token_ids'] = []
        state.pop('loaded_suspicious_box_ids', None)
        ctx['active'] = self.complete(state)
        ctx['active']['suspicious_token_ids'] = []
        ctx = commit(ctx, [TITLE])
        docs = export_documents(ctx)
        self.assertEqual(docs['review_suspicious_details.json'], {})
        self.assertNotIn('issue_type', docs['review_text_annotations.json'][0])
        self.assertEqual(build_summary(ctx)['images'][0]['fixed_stages'], ['status_and_order'])

    def test_content_correction_preserves_existing_suspicious_markers(self):
        self.documents[0]['issue_type'] = ['suspicious_content']
        detail = dict(issue_type='suspicious_content', box_ids=[1], note='Content may be incorrect.')
        self.write_zip(suspicious={'1': detail})
        baseline = load_dataset(self.zip, self.images)
        ctx = new_session(baseline)
        ctx['active'] = self.engine.open_image(self.images[0], baseline['1.png'])
        state = self.engine.apply(self.fixed_state(ctx), 'field', dict(path=['content', TITLE], value='文寺'))
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        self.assertEqual(export_documents(ctx)['review_suspicious_details.json']['1'], detail)

    def test_normal_to_mismatch_and_back_replaces_instead_of_duplicates(self):
        ctx = self.active()
        state = self.fixed_state(ctx)
        state = self.engine.apply(state, 'field', dict(path=['content', TITLE], value='永寺楽'))
        state = self.engine.apply(state, 'next')
        state = self.engine.apply(state, 'confirm_source_mismatch', dict(issue_type='extra_text', note='Source includes an extra character'))
        state = self.engine.apply(state, 'sort_boxes')
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        docs = export_documents(ctx)
        self.assertEqual(len(docs['review_source_mismatches.json']), 1)
        self.assertEqual(len(docs['review_text_annotations.json']), 2)
        ctx = self.active(ctx)
        state = self.fixed_state(ctx)
        state = self.engine.apply(state, 'field', dict(path=['content', TITLE], value='永寺'))
        ctx['active'] = self.complete(state)
        ctx = commit(ctx, [TITLE])
        docs = export_documents(ctx)
        self.assertEqual(docs['review_source_mismatches.json'], [])
        self.assertEqual(len(docs['review_text_annotations.json']), 3)

    def test_export_round_trip_and_input_never_changes(self):
        original = self.zip.read_bytes()
        ctx = commit(self.active(), [TITLE], finish=True)
        data = export_archive(ctx)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(len(archive.namelist()), 5)
            self.assertTrue(all(name.startswith('review_') for name in archive.namelist()))
        reviewed = self.root / 'review_annotations.zip'
        reviewed.write_bytes(data)
        self.assertEqual(load_dataset(reviewed, self.images), self.baseline)
        self.assertEqual(self.zip.read_bytes(), original)

    def test_coordinate_only_other_mismatch_can_be_reviewed_without_content(self):
        doc = dict(image='1.png', inscription_code='1', issue_type=['other'],
                   note='Image is unrelated to the source', bounding_boxes={
                       key: {'bbox': box['bbox']} for key, box in self.documents[0]['bounding_boxes'].items()})
        self.write_zip(annotations=self.documents[1:], content=self.content[1:], mismatches=[doc])
        baseline = load_dataset(self.zip, self.images)
        ctx = new_session(baseline)
        ctx['active'] = self.engine.open_image(self.images[0], baseline['1.png'])
        ctx['active'] = self.complete(self.fixed_state(ctx))
        ctx = commit(ctx, [TITLE])
        self.assertEqual(ctx['committed']['1.png'], baseline['1.png'])

    def test_other_mismatch_preserves_imported_suspicious_markers(self):
        doc = dict(image='1.png', inscription_code='1', issue_type=['other', 'suspicious_content'],
                   note='Image unrelated to source', bounding_boxes={
                       key: {'bbox': box['bbox']} for key, box in self.documents[0]['bounding_boxes'].items()})
        detail = dict(issue_type='suspicious_content', box_ids=[1], note='Content may be incorrect.')
        self.write_zip(annotations=self.documents[1:], content=self.content[1:],
                       mismatches=[doc], suspicious={'1': detail})
        baseline = load_dataset(self.zip, self.images)
        ctx = new_session(baseline)
        ctx['active'] = self.engine.open_image(self.images[0], baseline['1.png'])
        ctx['active'] = self.complete(self.fixed_state(ctx))
        ctx = commit(ctx, [TITLE])
        self.assertEqual(ctx['committed']['1.png'], baseline['1.png'])

    def test_added_and_deleted_boxes_include_characters_and_both_ids(self):
        result = deepcopy(self.baseline['1.png'])
        result['origins']['2'] = None
        result['document']['bounding_boxes']['2']['bbox'] = [30, 30, 40, 40]
        result['document']['annotations']['2'] = '楽'
        changes = changes_between(self.baseline['1.png'], result)
        added = next(c for c in changes if c['type'] == 'box_added')
        deleted = next(c for c in changes if c['type'] == 'box_deleted')
        self.assertIsNone(added['original_box_id'])
        self.assertEqual(added['after']['character'], '楽')
        self.assertIsNone(deleted['final_box_id'])
        self.assertEqual(deleted['before']['character'], '寺')

    def test_conflicting_and_duplicate_imports_are_rejected(self):
        self.write_zip(annotations=self.documents + [self.documents[0]])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            load_dataset(self.zip, self.images)
        self.write_zip()
        with zipfile.ZipFile(self.zip, 'a') as archive:
            archive.writestr('review_text_annotations.json', json.dumps(self.documents))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            load_dataset(self.zip, self.images)

    def test_selection_and_history_use_intersection_but_export_keeps_missing_records(self):
        self.images[1].unlink()
        Image.new('RGB', (100, 100)).save(self.root / 'extra.png')
        options = app.parser().parse_args(['--input-zip', str(self.zip), '--image-dir', str(self.root), '--skip-detection'])
        ui = app.create_app(options)
        config = ui.get_config_file()
        picker = next(component['props'] for component in config['components']
                      if component['type'] == 'dropdown' and component['props'].get('label') == 'Image')
        self.assertEqual([choice[0] for choice in picker['choices']], ['1.png', '3.png'])
        baseline = load_dataset(self.zip, [self.images[0], self.images[2], self.root / 'extra.png'])
        self.assertEqual(export_documents(new_session(baseline))['review_text_annotations.json'], self.documents)
        self.assertEqual(export_documents(new_session(baseline))['review_inscription_content.json'], self.content)
        history = next(entry.fn for entry in ui.fns.values()
                       if entry.fn and any(getattr(output, 'elem_id', None) == 'history-results'
                                           for output in entry.outputs))
        markup = history(new_session(baseline))[1]
        self.assertIn('1.png', markup)
        self.assertIn('3.png', markup)
        self.assertNotIn('2.png', markup)
        self.assertNotIn('extra.png', markup)
        self.assertIn('Reviewed 0/2', markup)

    def test_no_matching_images_has_empty_picker_and_keeps_complete_export(self):
        empty = self.root / 'empty'
        empty.mkdir()
        options = app.parser().parse_args(['--input-zip', str(self.zip), '--image-dir', str(empty), '--skip-detection'])
        ui = app.create_app(options)
        components = ui.get_config_file()['components']
        start = next(component['props'] for component in components
                     if component['type'] == 'button' and component['props'].get('value') == 'Start Review')
        self.assertFalse(start['interactive'])
        baseline = load_dataset(self.zip, [])
        self.assertEqual(export_documents(new_session(baseline))['review_text_annotations.json'], self.documents)

    def test_coordinate_only_mismatch_without_image_is_preserved(self):
        document = dict(image='missing.png', inscription_code='missing', issue_type=['other'],
                        note='Unrelated source', bounding_boxes={'1': {'bbox': [10, 20, 30, 40]}})
        self.write_zip(mismatches=[document])
        baseline = load_dataset(self.zip, self.images)
        self.assertEqual(export_documents(new_session(baseline))['review_source_mismatches.json'], [document])

    def test_notes_and_sessions_are_independent(self):
        first, second = new_session(self.baseline), new_session(self.baseline)
        first['notes']['1.png'] = {'review': 'Saved before finishing'}
        self.assertEqual(build_summary(first)['images'][0]['result'], 'unreviewed')
        self.assertEqual(build_summary(second)['images'][0]['notes'], {})
        first['baseline']['1.png']['document']['annotations']['1'] = '文'
        self.assertEqual(second['baseline']['1.png']['document']['annotations']['1'], '永')

    def test_ui_callbacks_open_fix_notes_reset_and_finish(self):
        options = app.parser().parse_args(['--input-zip', str(self.zip), '--image-dir', str(self.root), '--skip-detection'])
        ui = app.create_app(options)
        functions = {entry.fn.__name__: entry.fn for entry in ui.fns.values() if entry.fn}
        opened = functions['open_image'](new_session(self.baseline), str(self.images[0].resolve()))
        ctx = opened[0]
        self.assertNotIn('baseline', ctx)
        self.assertEqual(ctx['active']['mode'], 'inspect')
        self.assertEqual(len(opened), 59)
        self.assertTrue(opened[54]['visible'])
        self.assertFalse(opened[40]['visible'])
        fixed = functions['start_fix'](ctx)
        self.assertEqual(fixed[0]['active']['current_step'], 2)
        self.assertTrue(fixed[57]['visible'])
        ctx, _ = functions['save_note'](fixed[0], 'content', 'A saved note')
        ctx, _ = functions['save_note'](ctx, 'review', 'Note remains attached to its image', '2.png')
        self.assertEqual(ctx['notes']['2.png']['review'], 'Note remains attached to its image')
        reset = functions['start_fix'](ctx, True)[0]
        self.assertEqual(reset['notes']['1.png']['content'], 'A saved note')
        ctx = functions['open_image'](reset, str(self.images[1].resolve()))[0]
        finished = functions['finish_image'](ctx)[0]
        self.assertIn('2.png', finished['committed'])


if __name__ == '__main__':
    unittest.main()
