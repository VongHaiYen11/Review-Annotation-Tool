"""Pure session operations; the browser owns the serialized session value."""
from copy import deepcopy
from .content import content_document
from .io import final_document, final_source_mismatch_document, state_order
from .state import new_state, source_mismatch_confirmed
from .summary import changes_between


def new_session(baseline):
    return dict(baseline=deepcopy(baseline), committed={}, drafts={}, notes={}, active=new_state())


def commit(ctx, titles, finish=False):
    ctx = deepcopy(ctx)
    state = ctx['active']
    image = state['image']
    if finish:
        if state.get('mode') != 'inspect':
            raise ValueError('Finish is only available in inspection mode.')
        result = deepcopy(ctx['committed'].get(image, ctx['baseline'][image]))
    else:
        if state.get('mode') != 'fix' or state['current_step'] != 7:
            raise ValueError('Complete Fix before saving the annotation.')
        mismatch = source_mismatch_confirmed(state)
        document = final_source_mismatch_document(state) if mismatch else final_document(state)
        content = content_document(image, state['code'], state['verified_content'] or state['draft_content'], titles)
        # A generated alignment source is an editing convenience, not new content.
        previous_content = state.get('loaded_content_document')
        original_source = state['source_content']['content']
        values = deepcopy(previous_content['content']) if previous_content else {}
        for field, value in content['content'].items():
            if value != original_source.get(field):
                values[field] = value
        content = dict(content, content=values) if previous_content or values else None
        mapping = state['region_uid_by_box_id']
        if mismatch and state['source_mismatch']['issue_type'] == 'other':
            mapping = {str(index): uid for index, uid in enumerate(state_order(state), 1)}
        result = dict(document=deepcopy(document), mismatch=mismatch, content=content,
                      origins={
                          box_id: state.get('original_box_id_by_region', {}).get(uid)
                          for box_id, uid in mapping.items()})
        if not changes_between(ctx['baseline'][image], result):
            result = deepcopy(ctx['baseline'][image])
    ctx['committed'][image] = result
    ctx['drafts'].pop(state['image_path'], None)
    ctx['active'] = new_state()
    ctx['active']['revision'] = state['revision'] + 1
    return ctx
