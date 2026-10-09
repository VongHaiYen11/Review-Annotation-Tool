"""New annotation schema, flag transitions, overlays and committed exports."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from annotation.bbox import add_bbox, sync_draft_boxes
from annotation.state import new_state, set_verified_content, initialize_alignment
from annotation.status import FLAGS, update_status, replace_statuses, confirm_status
from annotation.io import load_annotation, final_document, final_source_mismatch_document, validate_document
from annotation.export import export_archive
from annotation.dataset import load_dataset
from annotation.session import new_session, commit
from crop.crop import default_crop
from ui.presentation import workflow_progress, display_step
from annotation.workflow import Workflow
from ui.editor import snapshot


class StatusFlags(unittest.TestCase):
    def state(self):
        s = new_state()
        s.update(image='1.png', image_size=[100,100], current_step=4, image_url='image.jpg')
        set_verified_content(s, {}, '永寺')
        for x in (10,40):
            add_bbox(s, [x,10,x+20,30])
        initialize_alignment(s)
        return s

    def test_defaults_exclusion_and_intact(self):
        s = self.state(); uid = s['region_uid_by_box_id']['1']
        self.assertTrue(all(s['regions'][uid][flag] is False for flag in FLAGS))
        update_status(s,uid,'intact',unavailable_font=True)
        update_status(s,uid,'intact',expert_prediction=True)
        self.assertEqual(s['regions'][uid]['status'],'damaged')
        update_status(s,uid,'intact')
        self.assertTrue(s['regions'][uid]['expert_prediction'])
        self.assertTrue(s['regions'][uid]['unavailable_font'])
        update_status(s,uid,'damaged',unknown=True)
        self.assertTrue(s['regions'][uid]['unknown'])
        self.assertFalse(s['regions'][uid]['expert_prediction'])
        self.assertFalse(s['regions'][uid]['unavailable_font'])
        update_status(s,uid,'damaged',unavailable_font=True)
        self.assertFalse(s['regions'][uid]['unknown'])
        update_status(s,uid,'damaged',unknown=True)
        update_status(s,uid,'intact')
        self.assertFalse(s['regions'][uid]['unknown'])

    def test_complete_snapshot_preserves_intact_after_expert_toggle(self):
        s=self.state();uid=s['region_uid_by_box_id']['1']
        statuses={u:'intact' for u in s['regions']}
        experts={u:u==uid for u in s['regions']}
        replace_statuses(s,statuses,expert_predictions=experts)
        confirm_status(s)
        self.assertEqual(s['bounding_boxes']['1']['status'],'intact')
        self.assertTrue(s['bounding_boxes']['1']['expert_prediction'])
        before=deepcopy(s)
        with self.assertRaises(ValueError):
            replace_statuses(s,statuses,unavailable_fonts={uid:'true'})
        self.assertEqual(s,before)

    def overlay(self,s,step):
        s['current_step']=step
        markup=snapshot(s)['markup']
        start=markup.index('<svg class="annotation-canvas"');end=markup.index('</svg>',start)+6
        svg=ET.fromstring(markup[start:end]);ns={'svg':''}
        return svg.find(".//svg:g[@data-box-id='1']",ns), ns

    def test_python_overlay_colors_and_marks(self):
        for step in (4,7):
            for font,expert,unknown,expected in (
                (False,False,False,('#22c55e','#ffffff')),
                (True,False,False,('#22c55e','#ec4899')),
                (False,True,False,('#facc15','#facc15')),
                (True,True,False,('#facc15','#ec4899')),
                (False,False,True,('#ef4444','#ffffff'))):
                s=self.state();uid=s['region_uid_by_box_id']['1']
                s['regions'][uid].update(status='damaged' if unknown else 'intact',
                    unknown=unknown,unavailable_font=font,expert_prediction=expert)
                confirm_status(s)
                group,ns=self.overlay(s,step)
                rect=group.find('svg:rect',ns)
                label=group.find("svg:text[@data-box-order-label='1']",ns)
                self.assertEqual((rect.get('stroke'),label.get('fill')),expected)
                mark=group.find("svg:text[@data-unknown-mark='1']",ns)
                self.assertEqual(mark is not None,unknown)
                if unknown:
                    self.assertEqual((mark.text,mark.get('fill')),('?','#ef4444'))
                    self.assertEqual(mark.get('font-weight'),'700')
                    self.assertEqual(float(mark.get('font-size')),float(rect.get('width'))*0.80)
                if font:self.assertEqual((rect.get('fill'),rect.get('fill-opacity')),('#ec4899','.20'))

    def test_review_crop_resize_preserves_visuals_and_scales_question_mark(self):
        s=self.state();uid=s['region_uid_by_box_id']['1']
        # A reopened crop may have non-uniform image scale factors.
        s.update(crop=[5,5,95,95], loaded_crop_source=[5,5,95,95],
                 loaded_crop_scaled=[2,1,48,24], resized_image_size=[50,25])
        for font,expert,unknown in ((True,False,False),(True,True,False),(False,False,True)):
            s['regions'][uid].update(status='damaged',unknown=unknown,
                unavailable_font=font,expert_prediction=expert)
            confirm_status(s)
            group,ns=self.overlay(s,7)
            rect=group.find('svg:rect',ns)
            self.assertEqual(float(rect.get('width')),10)
            self.assertEqual(float(rect.get('height')),5)
            if font:
                self.assertEqual((rect.get('fill'),rect.get('fill-opacity')),('#ec4899','.20'))
            if expert:self.assertEqual(rect.get('stroke'),'#facc15')
            mark=group.find("svg:text[@data-unknown-mark='1']",ns)
            if unknown:
                self.assertEqual(float(mark.get('font-size')),4)
                self.assertEqual(float(mark.get('x')),float(rect.get('x'))+5)
                self.assertEqual(float(mark.get('y')),float(rect.get('y'))+2.5)
                self.assertEqual(mark.get('font-weight'),'700')

    def test_miss_clears_all_character_flags(self):
        s=self.state();uid=s['region_uid_by_box_id']['1']
        s['annotations']['1']='MISS'
        update_status(s,uid,'damaged',unavailable_font=True,expert_prediction=True)
        self.assertTrue(all(not s['regions'][uid][flag] for flag in FLAGS))
        group,ns=self.overlay(s,4)
        self.assertIsNotNone(group.find("svg:g[@data-miss-mark='1']",ns))
        self.assertIsNone(group.find("svg:text[@data-unknown-mark='1']",ns))

    def test_draft_geometry_and_alignment_preserve_flags(self):
        s=self.state();uid=s['region_uid_by_box_id']['1']
        update_status(s,uid,'intact',unavailable_font=True,expert_prediction=True)
        boxes=deepcopy(s['regions'])
        boxes[uid]['bbox']=[11,11,31,31]
        for index,u in enumerate(reversed(list(boxes)),1):boxes[u]['order']=index
        sync_draft_boxes(s,{'boxes':boxes})
        box_id=s['box_id_by_region'][uid]
        self.assertTrue(s['bounding_boxes'][box_id]['unavailable_font'])
        self.assertTrue(s['bounding_boxes'][box_id]['expert_prediction'])
        self.assertEqual(s['bounding_boxes'][box_id]['bbox'],[11,11,31,31])

    def test_workflow_save_reopen_export_normal_and_missing_extra(self):
        title = 'Nguyên văn chữ Hán Nôm'
        for text, issue in [('永寺', None), ('永', 'missing_text'), ('永寺樂', 'extra_text')]:
            with self.subTest(issue=issue), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                image = root / '1.png'
                Image.new('RGB', (100, 100)).save(image)
                initial = self.state()
                initial.update(code='1', image_path=str(image), crop=default_crop([100,100]))
                if issue:
                    initial['annotation_text'] = text
                    initial['source_mismatch'] = dict(issue_type=issue, note='', source_text=text,
                        source_character_count=len(text), bounding_box_count=2)
                    initialize_alignment(initial)
                initial['workflow'].update(status_valid=True, reading_order_valid=True)
                document = final_source_mismatch_document(initial) if issue else final_document(initial)
                baseline = {'1.png': dict(document=document, mismatch=bool(issue),
                    content=dict(image='1.png', inscription_code='1', content={title: text}),
                    origins={'1':'1', '2':'2'})}
                engine = Workflow(SimpleNamespace(content_titles=[title], annotation_title=title))
                self.addCleanup(engine._preview_cache.cleanup)
                ctx = new_session(baseline)
                state = engine.open_image(image, baseline['1.png'])
                state.update(mode='fix', current_step=2)
                state = engine.apply(state, 'next')
                state = engine.apply(state, 'next')
                # Change only flags; Save must classify this as a real edit.
                state = engine.apply(state, 'status', dict(id='1', status='intact',
                    unavailable_font=True, expert_prediction=True, suspicious=True))
                state = engine.apply(state, 'status', dict(id='1', status='intact'))
                while state['current_step'] != 7:
                    state = engine.apply(state, 'next')
                reviewed_svg = self.overlay(state, 7)[0]
                ctx['active'] = state
                ctx = commit(ctx, [title])
                saved = ctx['committed']['1.png']
                reopened = engine.open_image(image, saved)
                box = reopened['bounding_boxes']['1']
                self.assertEqual((box['status'], box['unavailable_font'], box['expert_prediction']),
                                 ('intact', True, True))
                self.assertTrue(box['suspicious'])
                # Initial inspection and Review share the same transformed overlay.
                self.assertTrue(snapshot(reopened)['readOnly'])
                self.assertEqual(ET.tostring(self.overlay(reopened, 7)[0]), ET.tostring(reviewed_svg))
                archive = root / 'review.zip'
                archive.write_bytes(export_archive(ctx))
                exported = load_dataset(archive, [image])
                self.assertEqual(exported['1.png']['document']['bounding_boxes']['1'], box)
                reopened = engine.open_image(image, saved)
                reopened.update(mode='fix', current_step=2)
                while reopened['current_step'] != 7:
                    reopened = engine.apply(reopened, 'next')
                ctx['active'] = reopened
                saved_again = commit(ctx, [title])['committed']['1.png']
                self.assertEqual(saved_again, saved)
                self.assertEqual(saved_again['document'].get('issue_type'), [issue] if issue else None)
                if not issue:
                    invalid = deepcopy(saved['document'])
                    del invalid['bounding_boxes']['1']['unavailable_font']
                    path = root / 'invalid.json'
                    path.write_text(json.dumps(invalid), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'unavailable_font'):
                        load_annotation(path, '1.png', [100,100])

    def test_import_rejects_old_schema_and_invalid_flags(self):
        state = self.state()
        state.update(crop=default_crop([100,100]))
        state['workflow'].update(status_valid=True, reading_order_valid=True)
        document = final_document(state)
        for flag in FLAGS:
            for value in (None, 'true', 1):
                invalid = deepcopy(document)
                invalid['bounding_boxes']['1'][flag] = value
                with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                    validate_document(invalid, '1.png', [100,100])
            invalid = deepcopy(document)
            del invalid['bounding_boxes']['1'][flag]
            with self.subTest(missing=flag), self.assertRaises(ValueError):
                validate_document(invalid, '1.png', [100,100])
        for values in (dict(status='unknown'), dict(unknown=True),
                       dict(status='damaged', unknown=True, expert_prediction=True),
                       dict(status='damaged', unknown=True, unavailable_font=True),
                       dict(status='damaged', unknown=True, suspicious=True)):
            invalid = deepcopy(document)
            invalid['bounding_boxes']['1'].update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_document(invalid, '1.png', [100,100])
        for flag in FLAGS:
            invalid = deepcopy(document)
            invalid['annotations']['1'] = 'MISS'
            invalid['bounding_boxes']['1'].update(status='damaged', **{flag: True})
            with self.subTest(miss_flag=flag), self.assertRaisesRegex(ValueError, 'MISS'):
                validate_document(invalid, '1.png', [100,100])

    def test_archive_all_categories_and_empty_arrays(self):
        ctx = new_session({})
        with zipfile.ZipFile(io.BytesIO(export_archive(ctx))) as archive:
            self.assertEqual(set(archive.namelist()), {'text_annotations.json',
                'inscription_content.json', 'source_mismatches.json', 'review_summary.json'})
            for name in ('text_annotations.json', 'source_mismatches.json', 'inscription_content.json'):
                self.assertEqual(json.loads(archive.read(name)), [])
            summary = json.loads(archive.read('review_summary.json'))
            self.assertEqual(summary['images'], [])
            self.assertEqual(summary['counts']['total'], 0)

    def test_suspicious_transitions_and_unknown_exclusion(self):
        s = self.state(); uid = s['region_uid_by_box_id']['1']
        update_status(s, uid, 'damaged', unknown=True)
        update_status(s, uid, 'damaged', suspicious=True)
        self.assertFalse(s['regions'][uid]['unknown'])
        update_status(s, uid, 'intact', unavailable_font=True, expert_prediction=True)
        update_status(s, uid, 'intact')
        self.assertTrue(s['regions'][uid]['suspicious'])
        group, ns = self.overlay(s, 4)
        self.assertEqual(group.find('svg:rect', ns).get('fill'), '#ec4899')
        update_status(s, uid, 'damaged', unknown=True)
        self.assertTrue(s['regions'][uid]['unknown'])
        self.assertTrue(all(not s['regions'][uid][flag] for flag in FLAGS[1:]))

    def test_draft_apply_and_reorder_preserve_sequence_and_region_flags(self):
        from annotation.reading_order import update_text_sequence
        s = self.state(); uids = list(s['regions'])
        update_text_sequence(s, ['寺', '永'])
        update_status(s, uids[0], 'intact', suspicious=True)
        boxes = deepcopy(s['regions'])
        for index, uid in enumerate(reversed(uids), 1):
            boxes[uid]['order'] = index
        sync_draft_boxes(s, {'boxes': boxes}, materialize_alignment=False)
        sync_draft_boxes(s, {'boxes': boxes})
        self.assertEqual(s['text_sequence'], ['寺', '永'])
        self.assertTrue(s['regions'][uids[0]]['suspicious'])
        self.assertTrue(s['bounding_boxes'][s['box_id_by_region'][uids[0]]]['suspicious'])

    def test_same_count_replacement_resets_alignment_and_preserves_survivor(self):
        from annotation.reading_order import update_text_sequence
        s = self.state(); uids = list(s['regions'])
        update_text_sequence(s, ['寺', '永'])
        update_status(s, uids[0], 'intact', suspicious=True)
        boxes = {uids[0]: dict(s['regions'][uids[0]], order=1),
                 'replacement': dict(bbox=[70,10,90,30], status='intact', order=2,
                     **dict.fromkeys(FLAGS, False))}
        sync_draft_boxes(s, {'boxes': boxes})
        self.assertEqual(s['text_sequence'], ['永', '寺'])
        self.assertTrue(s['regions'][uids[0]]['suspicious'])
        self.assertNotIn(uids[1], s['regions'])

    def test_progress_depends_on_current_step(self):
        for step in range(1, 8):
            for flags in (False, True):
                state = self.state()
                state['current_step'] = step
                state['workflow'] = dict.fromkeys(state['workflow'], flags)
                state.update(saved=flags, crop_saved=flags)
                markup = workflow_progress(state)
                self.assertEqual(markup.count('is-complete'), display_step(step)-1)
                self.assertEqual(markup.count('is-active'), 1)
                self.assertEqual(markup.count('aria-current="step"'), 1)
        self.assertEqual(workflow_progress(dict(current_step=7)).count('is-complete'), 5)

    def test_miss_mark_scales_with_review_box(self):
        state = self.state()
        state['annotations']['1'] = 'MISS'
        state.update(crop=[5,5,95,95], loaded_crop_source=[5,5,95,95],
                     loaded_crop_scaled=[2,1,48,24], resized_image_size=[50,25])
        for step in (4, 7):
            group, ns = self.overlay(state, step)
            rect = group.find('svg:rect', ns)
            mark = group.find("svg:g[@data-miss-mark='1']", ns)
            lines = mark.findall('svg:line', ns)
            x, y = float(rect.get('x')), float(rect.get('y'))
            width, height = float(rect.get('width')), float(rect.get('height'))
            self.assertEqual(len(lines), 2)
            self.assertAlmostEqual(float(lines[0].get('x1')), x + width*0.2)
            self.assertAlmostEqual(float(lines[0].get('y1')), y + height*0.2)
            self.assertAlmostEqual(float(lines[0].get('x2')), x + width*0.8)
            self.assertAlmostEqual(float(lines[0].get('y2')), y + height*0.8)
            self.assertEqual(mark.get('stroke'), '#ef4444')
            self.assertEqual(mark.get('stroke-width'), '2.25')
            self.assertTrue(all(line.get('vector-effect') == 'non-scaling-stroke' for line in lines))

if __name__=='__main__':unittest.main()
