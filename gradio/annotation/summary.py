"""Semantic differences between imported and committed image results."""
from copy import deepcopy

STAGES = ('content', 'bounding_boxes', 'status_and_order', 'crop', 'review')


def changes_between(baseline, result):
    changes = []

    def change(stage, kind, before, after, **details):
        if before != after:
            changes.append(dict(stage=stage, type=kind, **details,
                                before=deepcopy(before), after=deepcopy(after)))

    old, new = baseline['document'], result['document']
    before_content = (baseline.get('content') or {}).get('content', {})
    after_content = (result.get('content') or {}).get('content', {})
    for field in sorted(set(before_content) | set(after_content)):
        change('content', 'content_changed', before_content.get(field), after_content.get(field), field=field)
    matched = set()
    for final_id, box in new['bounding_boxes'].items():
        original_id = result.get('origins', {}).get(final_id)
        details = dict(original_box_id=original_id, final_box_id=final_id)
        if original_id not in old['bounding_boxes']:
            change('bounding_boxes', 'box_added', None,
                   dict(box, character=new.get('annotations', {}).get(final_id)), **details)
            continue
        matched.add(original_id)
        original = old['bounding_boxes'][original_id]
        change('bounding_boxes', 'box_updated', original['bbox'], box['bbox'], **details)
        change('status_and_order', 'reading_order_changed', int(original_id), int(final_id), **details)
        change('status_and_order', 'character_changed', old.get('annotations', {}).get(original_id),
               new.get('annotations', {}).get(final_id), **details)
        change('status_and_order', 'status_changed',
               {key: original.get(key) for key in ('status', 'unknown', 'unavailable_font', 'expert_prediction')},
               {key: box.get(key) for key in ('status', 'unknown', 'unavailable_font', 'expert_prediction')}, **details)
    for original_id in sorted(set(old['bounding_boxes']) - matched, key=int):
        change('bounding_boxes', 'box_deleted',
               dict(old['bounding_boxes'][original_id], character=old.get('annotations', {}).get(original_id)),
               None, original_box_id=original_id, final_box_id=None)
    for field in ('crop', 'image_resize'):
        change('crop', field + '_changed', old.get(field), new.get(field))
    def mismatch_details(bundle):
        if not bundle['mismatch']:
            return None
        doc = bundle['document']
        values = {key: doc[key] for key in ('note', 'source_text', 'excluded_characters') if key in doc}
        issues = doc.get('issue_type', [])
        values['issue_type'] = [value for value in (issues if isinstance(issues, list) else [issues])
                                if value != 'suspicious_content']
        return values
    change('bounding_boxes', 'source_mismatch_changed', mismatch_details(baseline), mismatch_details(result))
    change('status_and_order', 'suspicious_details_changed', baseline.get('suspicious'), result.get('suspicious'))
    return changes


def build_summary(ctx):
    images = []
    counts = dict(total=len(ctx['baseline']), accepted=0, fixed=0, unreviewed=0)
    for image, baseline in ctx['baseline'].items():
        result = ctx['committed'].get(image)
        changes = changes_between(baseline, result) if result else []
        outcome = ('fixed' if changes else 'accepted') if result else 'unreviewed'
        counts[outcome] += 1
        images.append(dict(image=image, result=outcome,
                           fixed_stages=[stage for stage in STAGES if any(c['stage'] == stage for c in changes)],
                           changes=changes, notes=deepcopy(ctx['notes'].get(image, {}))))
    return dict(counts=counts, images=images)
