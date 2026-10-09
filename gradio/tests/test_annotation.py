import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from annotation.state import (new_state, set_verified_content, refresh_bbox_validation,
                              initialize_alignment, source_mismatch_confirmed)
from annotation.bbox import (add_bbox, update_bbox, update_bboxes, delete_bbox,
                             sync_draft_boxes)
from annotation.text_alignment import count_annotation_characters, normalize_annotation_text
from annotation.reading_order import (update_text_sequence, update_text_tokens,
                                      build_text_sequence,
                                      validate_reading_order)
from annotation.status import update_status, replace_statuses, confirm_status
from annotation.io import (final_document, final_source_mismatch_document, load_annotation,
                           load_source_mismatch, read_json)
from annotation.workflow import Workflow, annotations_to_text
from ui.editor import snapshot
from crop.crop import (auto_scale_crop, crop_document,
                       default_crop, MAX_CROP_SIDE)
from PIL import Image


def state(text='永寺樂',n=3):
    s=new_state();s.update(image='12305.png',image_size=[100,100],current_step=3)
    set_verified_content(s,{},text)
    for i in range(n):add_bbox(s,[i*10,0,i*10+9,9])
    refresh_bbox_validation(s)
    return s


def aligned_state(text='永寺樂', n=3):
    s = state(text, n)
    confirm_status(s)
    initialize_alignment(s)
    return s


class Invariants(unittest.TestCase):
    def test_saved_annotations_are_concatenated_in_numeric_box_order(self):
        self.assertEqual(annotations_to_text({'10':'庚','2':'乙','1':'甲'}),'甲乙庚')

    def test_count_cases(self):
        self.assertTrue(state()['workflow']['bbox_valid'])
        self.assertFalse(state()['workflow']['alignment_valid'])
        self.assertFalse(state('永寺樂文')['workflow']['bbox_valid'])
        self.assertFalse(state(n=4)['workflow']['bbox_valid'])

    def test_same_length_edit(self):
        s=state();set_verified_content(s,{},'永樂寺')
        self.assertFalse(s['workflow']['alignment_valid']);self.assertEqual(s['annotations'],{})
        refresh_bbox_validation(s);self.assertEqual(s['annotations'],{})
        confirm_status(s);initialize_alignment(s)
        self.assertEqual(s['annotations'],{'1':'永','2':'樂','3':'寺'})

    def test_changed_count_and_add(self):
        s=state();s['workflow']['reading_order_valid']=True
        set_verified_content(s,{},'永樂寺文');refresh_bbox_validation(s)
        self.assertFalse(s['workflow']['bbox_valid']);self.assertFalse(s['workflow']['reading_order_valid'])
        add_bbox(s,[40,0,49,9]);refresh_bbox_validation(s)
        self.assertEqual(s['annotations'],{})
        confirm_status(s);initialize_alignment(s)
        self.assertEqual(s['annotations'],{'1':'永','2':'樂','3':'寺','4':'文'})
        self.assertFalse(s['workflow']['reading_order_valid'])

    def test_region_identity_is_internal_and_public_ids_are_rebuilt(self):
        s=aligned_state();uids=list(s['regions'])
        update_status(s,uids[0],'damaged')
        delete_bbox(s,uids[1])
        self.assertEqual(len(s['regions']),2)
        self.assertNotIn('2',s['bounding_boxes']);self.assertNotIn(2,s['reading_order'])
        self.assertEqual(s['regions'][uids[0]]['status'],'damaged')
        new_uid=add_bbox(s,[40,0,49,9]);self.assertNotIn(new_uid,uids)
        self.assertEqual(s['regions'][new_uid]['status'],'intact')
        refresh_bbox_validation(s);confirm_status(s);initialize_alignment(s)
        self.assertEqual(set(s['bounding_boxes']),{'1','2','3'})
        self.assertEqual(s['reading_order'],[1,2,3])
        self.assertEqual(sum(box['status']=='damaged' for box in s['bounding_boxes'].values()),1)

    def test_geometry_edit_preserves_public_ids_until_explicit_rebuild(self):
        s=aligned_state();uid=next(iter(s['regions']))
        update_bbox(s,uid,[1,1,8,8])
        self.assertEqual(s['bounding_boxes'][s['box_id_by_region'][uid]]['bbox'],[1,1,8,8])
        self.assertEqual(len(s['annotations']),3)
        self.assertEqual(s['reading_order'],[1,2,3])
        refresh_bbox_validation(s);confirm_status(s);initialize_alignment(s)
        self.assertEqual(set(s['bounding_boxes']),{'1','2','3'})

    def test_frontend_batch_box_commit_is_atomic(self):
        s=aligned_state();uids=list(s['regions']);before=deepcopy(s)
        update_bboxes(s,{uids[0]:[1,1,8,8],uids[1]:[11,1,18,8]},
                      active=uids[1],selected=[uids[0],uids[1]])
        self.assertEqual(s['regions'][uids[0]]['bbox'],[1,1,8,8])
        self.assertEqual(s['regions'][uids[1]]['bbox'],[11,1,18,8])
        self.assertEqual(s['selected_region_uids'],[uids[0],uids[1]])
        self.assertEqual(s['selected_region_uid'],uids[1])
        self.assertEqual(s['annotations'],{})
        invalid=deepcopy(before)
        with self.assertRaises(ValueError):
            update_bboxes(invalid,{uids[0]:[2,2,7,7],uids[1]:[0,0,101,10]})
        self.assertEqual(invalid,before)

    def test_frontend_snapshot_replaces_regions_and_preserves_metadata(self):
        s=state();uids=list(s['regions'])
        snapshot={
            uids[0]:dict(s['regions'][uids[0]],bbox=[1,1,8,8],order=4,
                         character='永',custom_flag='kept'),
            'box_new_1':dict(bbox=[40,0,49,9],status='damaged',unknown=True,
                             order=None,character=None),
        }
        sync_draft_boxes(s,{'boxes':snapshot,'active':'box_new_1',
                            'selected':['box_new_1']})
        self.assertEqual(set(s['regions']),{uids[0],'box_new_1'})
        self.assertEqual(s['regions'][uids[0]]['custom_flag'],'kept')
        self.assertEqual(s['regions']['box_new_1']['status'],'damaged')
        self.assertEqual(s['regions']['box_new_1']['order'],None)
        self.assertEqual(s['selected_region_uid'],'box_new_1')

    def test_empty_frontend_snapshot_does_not_restore_backend_regions(self):
        s=state()
        sync_draft_boxes(s,{'boxes':{},'active':None,'selected':[]})
        self.assertEqual(s['regions'],{})
        self.assertEqual(s['bounding_boxes'],{})

    def test_box_snapshot_keeps_stale_mismatch_removable_but_unconfirmed(self):
        s=state('永寺樂文',n=3)
        s['source_mismatch']={
            'source_text':s['annotation_text'],
            'source_character_count':4,
            'bounding_box_count':3,
            'issue_type':'missing_text',
            'note':'draft',
        }
        self.assertTrue(source_mismatch_confirmed(s))
        original=deepcopy(s['regions'])
        remaining=dict(list(s['regions'].items())[:2])
        sync_draft_boxes(s,{'boxes':remaining,'active':None,'selected':[]})
        self.assertIsNotNone(s['source_mismatch'])
        self.assertFalse(source_mismatch_confirmed(s))
        sync_draft_boxes(s,{'boxes':original,'active':None,'selected':[]},
                         materialize_alignment=False)
        self.assertFalse(source_mismatch_confirmed(s))

    def test_clearing_order_preserves_confirmed_mismatch(self):
        s=state('永寺樂文',n=3)
        s['source_mismatch']={
            'source_text':s['annotation_text'],
            'source_character_count':4,
            'bounding_box_count':3,
            'issue_type':'extra_text',
            'note':'',
        }
        boxes={uid:dict(box,order=index) for index,(uid,box)
               in enumerate(s['regions'].items(),1)}
        sync_draft_boxes(s,{'boxes':boxes,'active':None,'selected':[]},
                         materialize_alignment=False)
        self.assertTrue(source_mismatch_confirmed(s))
        confirmed=deepcopy(s['source_mismatch'])
        cleared={uid:dict(box,order=None) for uid,box in boxes.items()}
        sync_draft_boxes(s,{'boxes':cleared,'active':None,'selected':[]},
                         materialize_alignment=False)
        self.assertEqual(s['source_mismatch'],confirmed)
        self.assertTrue(source_mismatch_confirmed(s))
        self.assertFalse(s['workflow']['bbox_valid'])

    def test_replacing_box_at_same_count_requires_mismatch_confirmation(self):
        s=state('永寺樂文',n=3)
        s['source_mismatch']={
            'source_text':s['annotation_text'],
            'source_character_count':4,
            'bounding_box_count':3,
            'issue_type':'extra_text',
            'note':'',
        }
        boxes=dict(list(s['regions'].items())[1:])
        boxes['replacement']=dict(bbox=[40,0,49,9],status='intact',unknown=False)
        sync_draft_boxes(s,{'boxes':boxes,'active':None,'selected':[]},
                         materialize_alignment=False)
        self.assertFalse(source_mismatch_confirmed(s))

    def test_clear_source_mismatch_disables_sort_until_counts_match_or_reconfirm(self):
        engine=Workflow.__new__(Workflow)
        s=state('永寺樂文',n=3)
        s['current_step']=3
        s['source_mismatch']={
            'source_text':s['annotation_text'],
            'source_character_count':4,
            'bounding_box_count':3,
            'issue_type':'missing_text',
            'note':'',
        }
        self.assertTrue(source_mismatch_confirmed(s))
        cleared=engine.apply(s,'clear_source_mismatch')
        self.assertFalse(source_mismatch_confirmed(cleared))
        self.assertFalse(cleared['workflow']['bbox_valid'])
        with self.assertRaisesRegex(ValueError,'confirm a source mismatch'):
            engine.apply(cleared,'sort_boxes')

    def test_complete_mismatch_order_materializes_character_tokens(self):
        s=state('永寺',n=3)
        s['source_mismatch']={
            'source_text':s['annotation_text'],
            'source_character_count':2,
            'bounding_box_count':3,
            'issue_type':'missing_text',
            'note':'',
        }
        boxes={}
        for order,(uid,box) in enumerate(s['regions'].items(),1):
            boxes[uid]=dict(box,order=order)
        sync_draft_boxes(s,{'boxes':boxes,'active':next(iter(boxes)),
                            'selected':[next(iter(boxes))]})
        self.assertTrue(s['workflow']['alignment_valid'])
        self.assertEqual(list(s['annotations'].values()),['永','寺','MISS'])
        self.assertEqual(s['text_sequence'],['永','寺','MISS'])
        self.assertEqual(s['text_token_ids'],['1','2','3'])

    def test_reorder_assigns_character_tokens_to_coordinate_slots(self):
        s=aligned_state()
        statuses={key:box['status'] for key,box in s['bounding_boxes'].items()}
        geometry=deepcopy(s['bounding_boxes'])
        update_text_tokens(s,['永','樂','寺'],['1','3','2'])
        self.assertEqual(s['annotations'],{'1':'永','2':'樂','3':'寺'})
        self.assertEqual(s['reading_order'],[1,2,3])
        self.assertEqual(s['bounding_boxes'],geometry)
        self.assertEqual(
            {key:box['status'] for key,box in s['bounding_boxes'].items()},
            statuses)
        self.assertEqual(build_text_sequence(s),'永樂寺')
        for token_order in (['1','3'],['1','3','3'],['1','3','5']):
            with self.assertRaises(ValueError):
                update_text_tokens(s,['永','樂','寺'],token_order)

    def test_text_sequence_is_assigned_to_spatially_sorted_boxes(self):
        s=aligned_state()
        statuses={key:box['status'] for key,box in s['bounding_boxes'].items()}
        update_text_sequence(s,['永','樂','寺'])
        self.assertEqual(s['reading_order'],[1,2,3])
        self.assertEqual(s['annotations'],{'1':'永','2':'樂','3':'寺'})
        self.assertEqual(build_text_sequence(s),'永樂寺')
        self.assertEqual(
            {key:box['status'] for key,box in s['bounding_boxes'].items()},
            statuses)
        for sequence in (['永','樂'],['永','樂','樂'],['永','樂',3],None):
            with self.assertRaises(ValueError):update_text_sequence(s,sequence)

    def test_suspicious_stays_on_box_with_duplicate_character_tokens(self):
        s=aligned_state('永永寺')
        geometry=deepcopy(s['bounding_boxes'])
        update_status(s,s['region_uid_by_box_id']['2'],'intact',suspicious=True)
        geometry=deepcopy(s['bounding_boxes'])
        s['selected_token_id']='2';s['selected_box_id']='2'
        self.assertTrue(s['bounding_boxes']['2']['suspicious'])
        update_text_tokens(s,['永','永','寺'],['2','1','3'])
        self.assertTrue(s['bounding_boxes']['2']['suspicious'])
        self.assertFalse(s['bounding_boxes']['1']['suspicious'])
        self.assertEqual(s['selected_box_id'],'1')
        self.assertEqual(s['bounding_boxes'],geometry)

    def test_larger_token_shift_reassigns_every_intervening_slot(self):
        s=aligned_state('甲乙丙丁',4)
        geometry=deepcopy(s['bounding_boxes'])
        update_text_tokens(s,['丁','甲','乙','丙'],['4','1','2','3'])
        self.assertEqual(s['annotations'],{
            '1':'丁','2':'甲','3':'乙','4':'丙',
        })
        self.assertEqual(s['bounding_boxes'],geometry)

    def test_missing_markers_display_in_review_and_initial_text(self):
        from ui.editor import source_text
        from ui.review import content_markup
        s = state(n=6)
        s['source_mismatch'] = dict(source_text=s['annotation_text'],
            source_character_count=3, bounding_box_count=6,
            issue_type='missing_text', note='')
        initialize_alignment(s)
        update_text_sequence(s, ['永', 'MISS', 'MISS', 'MISS', '寺', '樂'])
        s.update(current_step=7, image_url='image.jpg')
        expected = '永&lt;miss&gt; &lt;miss&gt; &lt;miss&gt;寺樂'
        self.assertIn(f'<p>{expected}</p>', snapshot(s)['markup'])
        s.update(current_step=4, mode='fix')
        self.assertIn(f'<p>{expected}</p>', source_text(s))
        bundle = dict(content=dict(content={'Text': '永MISSMISSMISS寺樂'}))
        self.assertIn(f'<p>{expected}</p>', content_markup(bundle))
        self.assertEqual(build_text_sequence(s), '永MISSMISSMISS寺樂')
        self.assertEqual(bundle['content']['content']['Text'], '永MISSMISSMISS寺樂')

    def test_missing_source_alignment_adds_reorderable_miss_tags(self):
        s=state(n=5)
        s['source_mismatch']={
            'source_text':s['annotation_text'], 'source_character_count':3,
            'bounding_box_count':5, 'issue_type':'missing_text', 'note':''}
        confirm_status(s);initialize_alignment(s)
        self.assertEqual(list(s['annotations'].values()),['永','寺','樂','MISS','MISS'])
        update_text_sequence(s,['永','MISS','寺','樂','MISS'])
        self.assertEqual(s['annotations'],{
            '1':'永','2':'MISS','3':'寺','4':'樂','5':'MISS'})
        self.assertEqual(build_text_sequence(s),'永MISS寺樂MISS')
        self.assertEqual(s['bounding_boxes']['2']['status'],'intact')
        self.assertEqual(s['bounding_boxes']['5']['status'],'intact')
        self.assertTrue(all(s['bounding_boxes'][key]['status'] != 'unknown'
                            for key in ('1','3','4')))
        update_status(s,s['region_uid_by_box_id']['2'],'damaged')
        self.assertEqual(s['bounding_boxes']['2']['status'],'damaged')
        self.assertEqual(s['reading_order'],[1,2,3,4,5])
        self.assertEqual(s['annotations']['2'],'MISS')
        self.assertEqual(s['annotations']['5'],'MISS')
        self.assertEqual(s['bounding_boxes']['2']['status'],'damaged')
        self.assertEqual(s['bounding_boxes']['5']['status'],'intact')
        self.assertEqual(s['bounding_boxes']['3']['status'],'intact')
        s['workflow']['reading_order_valid']=True
        confirm_status(s)
        s['code']='12305'
        document=final_source_mismatch_document(s)
        self.assertEqual(document['annotations']['2'],'MISS')
        self.assertNotIn('reading_order',document)
        invalid=deepcopy(document);invalid['bounding_boxes']['2']['status']='invalid'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.json';path.write_text(json.dumps(invalid),encoding='utf-8')
            with self.assertRaises(ValueError):
                load_source_mismatch(path,'12305.png',[100,100])

    def test_source_mismatch_keeps_suspicious_in_box_only(self):
        s=state(n=2)
        s['source_mismatch']={
            'source_text':s['annotation_text'],'source_character_count':3,
            'bounding_box_count':2,'issue_type':'extra_text','note':''}
        confirm_status(s);initialize_alignment(s)
        geometry=deepcopy(s['bounding_boxes'])
        update_status(s,s['region_uid_by_box_id']['1'],'intact',suspicious=True)
        geometry=deepcopy(s['bounding_boxes'])
        s['workflow']['reading_order_valid']=True
        confirm_status(s);s['code']='12305'
        document=final_source_mismatch_document(s)
        self.assertEqual(document['issue_type'],['extra_text'])
        self.assertEqual(document['bounding_boxes'],geometry)
        s['current_step']=7;s['image_url']='image.jpg'
        review=snapshot(s)['markup']
        self.assertIn('fill="#facc15"',review)
        self.assertIn('· suspicious</title>',review)
        self.assertIn('Suspicious content',review)
        self.assertIn('MISS content',review)

        invalid=deepcopy(document)
        invalid['issue_type']='extra_source_characters'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.json';path.write_text(json.dumps(invalid),encoding='utf-8')
            with self.assertRaises(ValueError):
                load_source_mismatch(path,'12305.png',[100,100])

    def test_status_only(self):
        s=state();uid=list(s['regions'])[1];old=deepcopy(s);update_status(s,uid,'damaged')
        old['regions'][uid]['status']='damaged'
        self.assertEqual(s,old)

    def test_replace_statuses_updates_every_region_atomically(self):
        s=aligned_state();uids=list(s['regions']);before=deepcopy(s)
        statuses={uids[0]:'damaged',uids[1]:'intact',uids[2]:'damaged'}
        replace_statuses(s,statuses)
        self.assertEqual(
            {uid:box['status'] for uid,box in s['regions'].items()}, statuses)
        self.assertEqual(
            {box_id:box['status'] for box_id,box in s['bounding_boxes'].items()},
            {s['box_id_by_region'][uid]:status for uid,status in statuses.items()})
        invalid=deepcopy(before)
        with self.assertRaises(ValueError):
            replace_statuses(invalid,{uids[0]:'damaged'})
        self.assertEqual(invalid,before)

    def test_confirm_status_resynchronizes_public_box_status(self):
        s=aligned_state()
        uid=list(s['regions'])[1]
        box_id=s['box_id_by_region'][uid]
        s['regions'][uid]['status']='damaged'
        s['bounding_boxes'][box_id]['status']='intact'
        confirm_status(s)
        self.assertEqual(s['bounding_boxes'][box_id]['status'],'damaged')

    def test_coordinates(self):
        for coords in ([1,1,0,0],[-1,0,2,2],[0,0,101,2],[0,0,float('nan'),2],[False,0,2,2]):
            candidate=state();uid=next(iter(candidate['regions']))
            with self.assertRaises(ValueError):update_bbox(candidate,uid,coords)

    def test_crop_dimensions_are_limited_to_4096(self):
        self.assertEqual(default_crop([5000, 3000]), [0, 0, 5000, 3000])
        valid = crop_document('scan.png', [500, 600, 4596, 4696], [6000, 6000])
        self.assertEqual(valid['crop']['bottom_right'], [4596, 4696])
        scaled, size = auto_scale_crop([0,0,5000,3000],[5000,3000])
        self.assertEqual(max(scaled[2]-scaled[0],scaled[3]-scaled[1]),MAX_CROP_SIDE)
        self.assertEqual(size,[4096,2458])

        s = aligned_state()
        s['image_size'] = [5000, 6000]
        s['crop'] = default_crop(s['image_size'])
        s['workflow']['reading_order_valid'] = True
        document=final_document(s)
        self.assertEqual(max(document['crop']['bottom_right']),MAX_CROP_SIDE)
        self.assertEqual(document['image_resize']['output_size'],[3413,4096])

    def test_unicode(self):
        self.assertEqual(count_annotation_characters(' 永、樂。寺\n(𨴦) '),4)
        self.assertEqual(count_annotation_characters('a\u0301 𨴦\U000E0100'),2)
        self.assertEqual(normalize_annotation_text('(永樂寺)'), '永樂寺')
        self.assertFalse(state('，。',0)['workflow']['alignment_valid'])

    def test_save_guards(self):
        s=state()
        with self.assertRaises(ValueError):final_document(s)
        confirm_status(s);initialize_alignment(s);update_text_sequence(s,['永','樂','寺']);s['workflow']['reading_order_valid']=True;confirm_status(s)
        self.assertEqual(final_document(s)['annotations']['2'],'樂')
        del s['annotations']['2']
        with self.assertRaises(ValueError):final_document(s)
