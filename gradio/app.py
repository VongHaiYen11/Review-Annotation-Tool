"""Run from any directory: python gradio/app.py --image-dir ... --input-zip ..."""
import argparse
import base64
import html
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# This directory deliberately is NOT a Python package named gradio.
import gradio as gr
from annotation.state import (new_state, source_mismatch_confirmed,
                              calculate_spatial_order, refresh_bbox_validation)
from annotation.reading_order import suspicious_box_ids
from annotation.workflow import Workflow
from annotation.io import (SUSPICIOUS_NOTE, final_document,
                           final_source_mismatch_document, load_image_list,
                           read_json)
from annotation.content import content_fields, normalize_content_titles
from annotation.text_alignment import count_annotation_characters
from annotation.export import export_payload
from annotation.dataset import load_dataset
from annotation.session import new_session, commit
from annotation.summary import build_summary, STAGES
from ui.review import content_markup
from ui.editor import snapshot, source_text, SCRIPT, CSS
from ui.presentation import (APP_CSS, app_identity, workflow_progress,
                             panel_heading, panel_summary, footer,
                             status_rows, SECTION_LABELS)
from ui.fonts import FONT_FILES
from ui.icons import WARNING

log = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]


def loading_markup(label='Loading…', visible=False):
    """The single, application-wide progress surface used for queued actions."""
    state = ' is-visible' if visible else ''
    return (
        f'<div id="global-loading" class="global-loading{state}" '
        'role="status" aria-live="polite" aria-busy="true">'
        '<div class="global-loading-card">'
        '<span class="global-loading-spinner" aria-hidden="true"></span>'
        f'<span>{html.escape(label)}</span>'
        '</div></div>'
    )


LOADING_HIDDEN = loading_markup()
FONT_PRELOAD_JS = f"""() => {{
    if (window.__vietnamicaFontsReady) return;
    window.__vietnamicaFontsReady = true;
    const overlay = document.getElementById('global-loading');
    const families = {json.dumps(list(FONT_FILES), ensure_ascii=False)};
    const sample = '漢字';
    const specs = families.map(family => `20px "${{family}}"`);
    if (specs.every(spec => document.fonts.check(spec, sample))) {{
        overlay?.classList.remove('is-visible');
        return;
    }}
    const label = overlay?.querySelector('span:not(.global-loading-spinner)');
    if (label) label.textContent = 'Loading Hán/Nôm fonts…';
    overlay?.classList.add('is-visible');
    Promise.all(specs.map(spec => document.fonts.load(spec, sample)))
        .catch(() => {{}}) // Continue with the browser's normal fallback if a font fails.
        .finally(() => overlay?.classList.remove('is-visible'));
}}"""
SHOW_LOADING_JS = """(...args) => {
    const el = document.getElementById('global-loading');
    const image = document.querySelector('#current-image-name')?.textContent?.trim() || 'picker';
    if (el) {
        const label = el.querySelector('span:not(.global-loading-spinner)');
        if (label) label.textContent = 'Loading…';
        el.classList.add('is-visible');
    }
    console.info('[verification-ui] loading shown', {image, visible: Boolean(el?.classList.contains('is-visible'))});
    return args;
}"""
HISTORY_FILTER_JS = """(...args) => {
    const root = document.querySelector('#history-modal');
    if (!root) return;
    const search = root.querySelector('#history-search input, #history-search textarea');
    const query = (search?.value || '').trim().toLocaleLowerCase();
    const selected = Array.isArray(args[1]) ? args[1] : ['Done', 'Not Done'];
    const showDone = selected.includes('Done');
    const showPending = selected.includes('Not Done');
    let shown = 0;
    root.querySelectorAll('.history-item').forEach(item => {
        const name = item.querySelector('.history-image-name')?.textContent || '';
        const matches = name.toLocaleLowerCase().includes(query)
            && (item.classList.contains('processed') ? showDone : showPending);
        item.hidden = !matches;
        if (matches) shown++;
    });
    const count = root.querySelector('#history-shown');
    if (count) count.textContent = shown;
    const empty = root.querySelector('.history-empty');
    if (empty) empty.hidden = shown > 0;
    const clear = root.querySelector('#history-search-clear');
    if (clear) clear.hidden = !query;
}"""
HISTORY_CLEAR_JS = """(selected) => {
    const root = document.querySelector('#history-modal');
    const input = root?.querySelector('#history-search input, #history-search textarea');
    if (input) {
        const prototype = input.tagName === 'TEXTAREA'
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
        if (setter) setter.call(input, ''); else input.value = '';
        input.dispatchEvent(new Event('input', {bubbles: true}));
    }
    let shown = 0;
    root?.querySelectorAll('.history-item').forEach(item => {
        const visible = item.classList.contains('processed')
            ? selected.includes('Done') : selected.includes('Not Done');
        item.hidden = !visible;
        if (visible) shown++;
    });
    const count = root?.querySelector('#history-shown');
    if (count) count.textContent = shown;
    const empty = root?.querySelector('.history-empty');
    if (empty) empty.hidden = shown > 0;
    const clear = root?.querySelector('#history-search-clear');
    if (clear) clear.hidden = true;
    return [''];
}"""


def snapshot_board_state_js(selection_index, label_text='Loading…', deselect=False):
    """Submit the live board state instead of a potentially stale bridge."""
    deselect_script = ("document.querySelector('#annotation-board')?.dispatchEvent(\n"
                       "    new CustomEvent('deselect-regions', {bubbles: true})\n"
                       ");" if deselect else '')
    return f"""(...args) => {{
        const el = document.getElementById('global-loading');
        if (el) {{
            const label = el.querySelector('span:not(.global-loading-spinner)');
            if (label) label.textContent = {json.dumps(label_text)};
            el.classList.add('is-visible');
        }}
        {deselect_script}
        // The editor owns the canvas; there is no #annotation-board wrapper.
        // Read the live SVG so drag/resize changes are committed before the
        // Gradio event sends the selection bridge to Python.
        const board = document.querySelector('.annotation-canvas')?.closest('.workbench-board')
            || document.querySelector('.annotation-canvas')?.parentElement;
        const cards = board?.querySelector('.order-chips');
        let snapshot = {{}};
        try {{ snapshot = JSON.parse(args[{selection_index}] || '{{}}'); }} catch (_) {{}}
        // Prefer the editor-owned synchronous snapshot. The Gradio textbox
        // value may still contain the state from the preceding browser event.
        if (board?.dataset?.localBoxesSnapshot) {{
            try {{ snapshot = JSON.parse(board.dataset.localBoxesSnapshot); }} catch (_) {{}}
        }}
        if (cards) {{
            snapshot.textSequence = [...cards.querySelectorAll('[data-order-chip]')]
                .map(card => card.dataset.character);
            snapshot.tokenOrder = [...cards.querySelectorAll('[data-order-chip]')]
                .map(card => card.dataset.tokenId);
            snapshot.suspiciousTokenIds = [...cards.querySelectorAll('[data-order-chip].suspicious')]
                .map(card => card.dataset.tokenId);
        }}
        // The hidden bridge is the canonical serialization of localBoxes.
        // DOM geometry is only a legacy fallback when no local snapshot exists.
        const groups = snapshot.boxes ? [] : [...(board?.querySelectorAll('.annotation-canvas [data-box-id]') || [])];
        const boxes = {{}};
        const statuses = {{}};
        const unknowns = {{}};
        for (const group of groups) {{
            const id = group.dataset.boxId;
            const rect = group.querySelector('rect:not([data-image-resize-handle])');
            if (!rect) continue;
            const x = Number(rect.getAttribute('x')), y = Number(rect.getAttribute('y'));
            const bbox = [x, y, x + Number(rect.getAttribute('width')),
                          y + Number(rect.getAttribute('height'))];
            if (id === 'crop') snapshot.crop = bbox; else {{
                boxes[id] = bbox;
                if (group.dataset.status === 'intact' || group.dataset.status === 'damaged') {{
                    statuses[id] = group.dataset.status;
                    unknowns[id] = group.dataset.status === 'damaged' && group.dataset.unknown === 'true';
                }}
            }}
        }}
        if (!snapshot.boxes && Object.keys(boxes).length) snapshot.boxes = boxes;
        if (Object.keys(statuses).length) snapshot.statuses = statuses;
        if (Object.keys(unknowns).length) snapshot.unknowns = unknowns;
        args[{selection_index}] = JSON.stringify(snapshot);
        return args;
    }}"""


HIDE_LOADING_JS = """() => {
    const el = document.getElementById('global-loading');
    const image = document.querySelector('#current-image-name')?.textContent?.trim() || 'picker';
    el?.classList.remove('is-visible');
    console.info('[verification-ui] loading hidden', {image, visible: Boolean(el?.classList.contains('is-visible'))});
}"""

FONT_LOADING_DONE_JS = HIDE_LOADING_JS


def _config_relative(config_path, value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Config field {field} must be a non-empty path string.')
    path = Path(value).expanduser()
    return str(path.resolve() if path.is_absolute() else (config_path.parent / path).resolve())


def resolve_app_paths(options):
    """Fill omitted CLI paths from config while preserving CLI precedence."""
    config_path = Path(options.config).expanduser().resolve()
    config = read_json(config_path)
    options.annotation_title = config.get('annotation_title', 'Nguyên văn chữ Hán Nôm')
    options.content_titles = normalize_content_titles(config.get('content_titles', [options.annotation_title]))
    options.metadata_fields = ()
    options.image_dir = str(Path(options.image_dir).expanduser().resolve())
    options.input_zip = str(Path(options.input_zip).expanduser().resolve())
    return options


def parser():
    p=argparse.ArgumentParser(description='Sino-Nôm annotation review tool')
    p.add_argument('--config', default=str(REPO_ROOT / 'configs/review.json'),
                   help='Review content settings.')
    p.add_argument('--image-dir', required=True, help='Folder containing original images.')
    p.add_argument('--input-zip', required=True, help='Original or Reviewed annotation ZIP.')
    p.add_argument('--skip-detection', action='store_true',
                   help='Disable detection; load existing annotations or draw boxes manually.')
    p.add_argument('--vague-det-config', default=str(REPO_ROOT / 'text_detection/models/ckpts/damage_detect.py'))
    p.add_argument('--vague-det-weights', default=str(REPO_ROOT / 'text_detection/models/ckpts/damage_detect.pth'))
    p.add_argument('--ocr-det-executable', default=str(REPO_ROOT / 'text_detection/models/dists/det_model/det_model'))
    p.add_argument('--det-batch-size', type=int, default=1)
    p.add_argument('--img-size', type=int, default=2048)
    p.add_argument('--conf-thres', type=float, default=.45)
    p.add_argument('--iou-thres', type=float, default=.2)
    p.add_argument('--show-detection-logs', action='store_true',
                   help='Show stdout/stderr from the packaged OCR detector process.')
    p.add_argument('--port', type=int, default=None,
                   help='Server port. When omitted, Gradio selects the first available port.')
    p.add_argument('--server-name', default='127.0.0.1')
    p.add_argument('--share', action='store_true', help='Create a public Gradio link.')
    return p


def create_app(options):
    options=resolve_app_paths(options)
    if hasattr(gr, 'set_static_paths'):
        gr.set_static_paths(paths=[path for path in FONT_FILES.values() if path.is_file()])
    engine=Workflow(options)
    if hasattr(gr, 'set_static_paths'):
        gr.set_static_paths(paths=[engine.preview_dir])
    skip_detection=getattr(options,'skip_detection',False)
    images=load_image_list(options.image_dir)
    dataset=load_dataset(options.input_zip, images)
    images=[path for path in images if path.name in dataset]
    startup='' if images else 'No images match the annotation records in the input ZIP.'
    initial=new_session(dataset)

    def with_baseline(ctx):
        # Use the immutable imported dataset when processing a request. Its
        # browser copy stays mounted separately, so edits do not resend it.
        return dict(ctx, baseline=dataset)

    def browser_context(ctx):
        return {key: value for key, value in ctx.items() if key != 'baseline'}

    with gr.Blocks(title='Sino-Nôm Review Tool', fill_width=True, analytics_enabled=False) as app:
        # JSON values live in the page. Callbacks receive transient copies;
        # unlike gr.State, review results are not retained in server sessions.
        gr.JSON(dataset, visible=False, elem_id='review-baseline')
        session=gr.JSON(browser_context(initial), visible=False, elem_id='review-session')
        with gr.Column(elem_id='header-stack', scale=0):
            with gr.Row(elem_id='topbar', scale=0):
                progress=gr.HTML(app_identity(initial['active']), elem_id='app-chrome')
                with gr.Row(elem_id='header-actions', scale=0):
                    note_open=gr.Button('Note', scale=0, elem_id='note-open', interactive=False)
                    history_open=gr.Button('History', elem_id='history-open',
                                           elem_classes=['icon-button'], scale=0)
                    save_all=gr.Button('Download All', variant='primary', scale=0,
                                        elem_id='save-all',
                                        interactive=True)
            workflow_chrome=gr.HTML(workflow_progress(initial['active']),
                                    elem_id='workflow-chrome')
        with gr.Group(visible=False, elem_id='history-modal') as history_modal:
            with gr.Column(elem_id='history-modal-card'):
                with gr.Row(elem_id='history-modal-heading'):
                    gr.Markdown('### Image History')
                    history_close=gr.Button('×', elem_id='history-close',
                                            elem_classes=['icon-button'], scale=0)
                with gr.Group(elem_id='history-search-wrap'):
                    history_search=gr.Textbox(
                        placeholder='Search image name…', show_label=False,
                        elem_id='history-search', container=False)
                    history_clear=gr.Button('×', elem_id='history-search-clear',
                                            min_width=0, size='sm')
                with gr.Row(elem_id='history-filter-row'):
                    gr.HTML('<span class="history-filter-label">Filter</span>')
                    history_filter=gr.CheckboxGroup(
                        ['Done', 'Not Done'], value=['Done', 'Not Done'],
                        show_label=False, container=False,
                        elem_id='history-filter')
                history_results=gr.HTML(elem_id='history-results')
        with gr.Group(visible=False, elem_id='note-dialog') as note_dialog:
            note_image=gr.Textbox(label='Image', interactive=False)
            note_stage=gr.Dropdown([
                ('Content', 'content'), ('Bounding Boxes & Sort', 'bounding_boxes'),
                ('Status & Order', 'status_and_order'), ('Crop', 'crop'), ('Review', 'review')],
                value='review', label='Stage', interactive=False)
            note_text=gr.Textbox(label='Evaluator note', lines=4)
            with gr.Row():
                note_save=gr.Button('Save note', variant='primary')
                note_close=gr.Button('Close')
        message=gr.Markdown(startup,visible=bool(startup),elem_id='action-message')
        download_payload=gr.Textbox(visible=False)
        # This remains mounted across every callback, so only one loading modal is shown.
        loading_modal=gr.HTML(value=LOADING_HIDDEN, elem_id='global-loading-host')
        with gr.Row(elem_id='workspace', scale=1):
            with gr.Column(elem_id='image-start', min_width=0, elem_classes='panel') as image_start:
                gr.Markdown('## Select an image')
                gr.Markdown('Choose an image to review its imported annotation.')
                image_choice=gr.Dropdown(choices=[(p.name,str(p.resolve())) for p in images],label='Image')
                open_button=gr.Button('Start Review', variant='primary', interactive=bool(images))
            with gr.Column(visible=False, elem_id='control-panel', min_width=0,
                           elem_classes='panel') as control_panel:
                heading=gr.HTML(panel_heading(initial['active']))
                with gr.Group(elem_classes=['section','sidebar-section','sidebar-component','current-image-section']):
                    gr.Markdown('### Current image')
                    current_image=gr.Markdown('—', elem_id='current-image-name')
                    reset_all=gr.Button('Reset all changes', visible=False, elem_id='reset-all')
                with gr.Group(visible=False,
                              elem_classes=['section','sidebar-section','sidebar-component','content-tools']) as content_actions:
                    gr.Markdown('### Content actions')
                    with gr.Column(elem_classes=['button-group','sidebar-action-stack']):
                        undo=gr.Button('Undo changes',min_width=0)
                with gr.Group(visible=False, elem_classes=['section','sidebar-step-stack']) as box_group:
                    box_id=gr.Dropdown(visible=False)
                    selection_bridge=gr.Textbox(value='{}',show_label=False,
                                                elem_id='selection-bridge',
                                                elem_classes='frontend-bridge')
                    x1=gr.Number(visible=False, elem_id='bbox-x1')
                    y1=gr.Number(visible=False, elem_id='bbox-y1')
                    x2=gr.Number(visible=False, elem_id='bbox-x2')
                    y2=gr.Number(visible=False, elem_id='bbox-y2')
                    update=gr.Button(visible=False)
                    with gr.Group(visible=True, elem_classes=['section','sidebar-section','sidebar-component','sort-widget-section']) as bbox_selection_group:
                        gr.Markdown('### Sort & Reading Order')
                        with gr.Row(elem_classes=['field-group']):
                            manual_order=gr.Number(label='Bounding Box Order #', min_width=0, elem_id='manual-box-order', precision=0)
                        manual_order_error=gr.HTML('', elem_id='manual-box-order-error')
                        with gr.Row(elem_classes=['button-group','sidebar-action-row','bbox-action-row']):
                            sort_boxes=gr.Button('Sort Boxes', variant='primary', size='sm',
                                                 min_width=0, interactive=False,
                                                 elem_id='sort-boxes')
                            clear_order=gr.Button('Clear Order', size='sm', min_width=0,
                                                  interactive=False,
                                                  elem_id='clear-box-orders')
                    gr.HTML('''<section class="selection-guide" aria-label="Selection Guide">
                        <h3>Selection Guide</h3>
                        <ul>
                          <li><kbd>Click</kbd><span>Select a single box.</span></li>
                          <li><kbd>Drag</kbd><span>Draw a selection area to select multiple boxes.</span></li>
                          <li><kbd>Ctrl/Cmd + Click</kbd><span>Add or remove individual boxes from the current selection.</span></li>
                          <li><kbd>Alt/Option + Drag</kbd><span>Create a new bounding box.</span></li>
                        </ul>
                    </section>''', elem_id='selection-guide-host')
                    delete=gr.Button('Delete Selected', size='sm', min_width=0,
                                     elem_id='delete-box')
                    detect_confirm=gr.Checkbox(value=False,visible=False)
                    detect=gr.Button('Run Detection', variant='primary',
                                     interactive=not skip_detection,elem_id='run-detection')
                    with gr.Group(visible=False,
                                  elem_classes=['section','sidebar-section','sidebar-component','mismatch-panel']) as mismatch_group:
                        gr.Markdown('### Box-Content Validation')
                        summary=gr.HTML(panel_summary(initial['active']),
                                        elem_id='validation-summary-host')
                        mismatch_type=gr.Dropdown([
                            ('Missing Content','missing_text'),
                            ('Extra Content','extra_text'),
                            ('Other','other'),
                        ],label='Issue type',filterable=False,
                           elem_id='mismatch-issue-type')
                        mismatch_note=gr.Textbox(
                            label='Note (required only for Other)',lines=2,max_lines=3)
                        reset_mismatch_ui=gr.Button(
                            'Reset mismatch fields',elem_id='reset-mismatch-ui',
                            elem_classes=['frontend-bridge'])
                        with gr.Row(elem_classes=['button-group','sidebar-action-row','mismatch-action-row']):
                            confirm_mismatch=gr.Button(
                                'Confirm Mismatch', variant='primary', min_width=0,
                                elem_id='confirm-source-mismatch')
                            clear_mismatch=gr.Button(
                                'Clear', visible=False, variant='secondary', min_width=0,
                                elem_id='clear-source-mismatch')
                with gr.Group(visible=False,
                              elem_classes=['section','sidebar-section','sidebar-component','selection-section']) as status_group:
                    gr.Markdown('### Selected region')
                    hide_canvas_overlays=gr.Checkbox(
                        value=False,label='Show image only',elem_id='show-image-only')
                    status_id=gr.Dropdown(visible=False)
                    status=gr.Radio(['intact','damaged'],value='intact',label='Selected box status',elem_id='status-radio')
                    unknown_status=gr.Radio(['False','True'],value='False',label='Unknown character (Damaged only)',interactive=True,elem_id='unknown-radio')
                    suspicious_toggle=gr.Checkbox(value=False,label='Suspicious annotation',interactive=False,elem_id='suspicious-toggle')
                    apply_changes=gr.Button('Apply Changes', variant='primary',
                                            elem_id='apply-status-changes')
                with gr.Group(visible=False,
                              elem_classes=['section','sidebar-section','sidebar-component','box-color-control']) as box_color_group:
                    gr.Markdown('### Box color')
                    box_color=gr.Dropdown(
                        ['White','Cyan','Amber','Violet','Pink'],
                        value='White',show_label=False,interactive=True,
                        filterable=False,container=False,
                        elem_id='bbox-color-palette')
                # Preserve the status-table callback slot without rendering the
                # redundant region table.
                status_table=gr.State([])
                source_text_group=gr.HTML(
                    value='', visible=False, elem_id='sidebar-source-text',
                    elem_classes=['section','sidebar-section','sidebar-component'])
                # Retain the legacy output slot; text sequence is committed by Next.
                apply_order=gr.State(None)
                order_text=gr.State('[]')
                with gr.Group(visible=False, elem_classes=['section','sidebar-section','sidebar-component']) as crop_group:
                    gr.Markdown('### Crop')
                    gr.Markdown('Adjust the orange crop frame. Oversized crops are scaled automatically on export.',
                                elem_classes='sidebar-help')
                    crop_coords=gr.Textbox(label='Coordinates [x1, y1, x2, y2]',
                                           elem_id='crop-coordinates')
                    apply_crop=gr.State(None)
            with gr.Column(visible=False, elem_id='main-workspace', min_width=0, scale=1,
                           elem_classes='panel') as main_workspace:
                with gr.Column(elem_id='workspace-body'):
                    with gr.Group(visible=False, elem_id='content-editor',elem_classes='section') as content_group:
                        gr.Markdown('## Content Verification')
                        # These are fixed record sections, so an editable/searchable
                        # combobox only delays committing a click selection.
                        field=gr.Dropdown(label='Section', filterable=False)
                        field_value=gr.Textbox(label='Content',lines=8, elem_classes='han-nom-text')
                        content_bridge=gr.Textbox(
                            value='{}',visible=False,elem_id='content-draft-bridge')
                        with gr.Row(elem_classes='button-group'):
                            apply_field=gr.Button('Save change', variant='primary')
                        with gr.Accordion('Content JSON', open=False, elem_classes='section'):
                            content_preview=gr.Code(
                                label='Content', language='json', interactive=False,
                                lines=12, max_lines=30, elem_classes='han-nom-json',
                            )
                    board=gr.HTML(value=snapshot(initial['active']),html_template='${value.markup}',css_template=CSS,js_on_load=SCRIPT, elem_id='annotation-board')
                    with gr.Group(visible=False, elem_id='final-json-previews',
                                  elem_classes=['section','final-json-previews']) as final_json_group:
                        with gr.Accordion('text_annotations.json', open=True,
                                          elem_classes='section'):
                            preview=gr.JSON(
                                label='Image JSON', visible=True,
                                elem_id='final-preview', elem_classes='han-nom-json')
                        with gr.Accordion('suspicious_details.json', open=False,
                                          elem_classes='section') as suspicious_json_section:
                            suspicious_preview=gr.JSON(
                                label='Suspicious Details', visible=True,
                                elem_id='suspicious-preview',
                                elem_classes='han-nom-json')
                        with gr.Accordion('source_mismatches.json', open=False,
                                          elem_classes='section') as source_mismatches_json_section:
                            source_mismatches_preview=gr.JSON(
                                label='Source Mismatches', visible=True,
                                elem_id='source-mismatches-preview',
                                elem_classes='han-nom-json')
        with gr.Column(visible=False, elem_id='inspection-screen') as inspection_screen:
            with gr.Row(elem_id='inspection-columns'):
                inspection_board=gr.HTML(value=snapshot(initial['active']), html_template='${value.markup}', css_template=CSS, js_on_load=SCRIPT, elem_id='inspection-board')
                inspection_content=gr.HTML(elem_id='inspection-content')
            with gr.Row(elem_id='inspection-actions'):
                finish=gr.Button('Finish', variant='primary')
                fix=gr.Button('Fix')
                inspection_picker=gr.Button('Select another image')
        with gr.Row(visible=False, elem_id='workflow-footer',elem_classes='button-group') as workflow_footer:
            back=gr.Button('Back', interactive=False, scale=0, elem_id='back-button')
            footer_label=gr.HTML(footer(initial['active']), elem_id='footer-step')
            save=gr.Button('Save Annotation',visible=False,variant='primary', scale=0, elem_id='save-image')
            next_button=gr.Button('Next',variant='primary',interactive=False, scale=0, elem_id='next-button')
        # Preserve callback output slots while removing the normalized-text component.
        normalized=gr.State(None)
        outputs=[session,progress,message,content_group,field,field_value,content_preview,normalized,board,box_group,box_id,x1,y1,x2,y2,status_group,status_id,status,apply_order,order_text,preview,crop_group,crop_coords,save,heading,summary,footer_label,content_actions,back,next_button,status_table,
                 mismatch_group,mismatch_type,mismatch_note,confirm_mismatch,clear_mismatch]
        outputs.append(box_color_group)
        outputs.append(workflow_chrome)
        outputs.append(loading_modal)
        outputs.extend([image_start,control_panel,main_workspace,workflow_footer,current_image])
        outputs.append(content_bridge)
        outputs.append(suspicious_toggle)
        outputs.append(source_text_group)
        outputs.extend([
            final_json_group,suspicious_preview,suspicious_json_section,
            source_mismatches_preview,source_mismatches_json_section,
        ])
        outputs.append(save_all)
        outputs.append(image_choice)
        outputs.extend([inspection_screen, inspection_board, inspection_content, reset_all, note_open])

        def render(ctx, msg=''):
            ctx=with_baseline(ctx)
            if msg:
                gr.Warning(html.unescape(msg.removeprefix(WARNING).strip()))
                msg=''
            s=ctx['active']; step=s['current_step']; has=bool(s.get('image'))
            inspecting=s.get('mode') == 'inspect'
            current_bundle=ctx['committed'].get(s.get('image'), ctx['baseline'].get(s.get('image')))
            fields=(content_fields(
                s['draft_content'],s['code'],engine.content_titles,engine.metadata_fields)
                if has else [])
            choices=[(SECTION_LABELS.get(field['title'],field['title']),json.dumps(field['path'],ensure_ascii=False)) for field in fields]
            chosen=choices[0][1] if choices else None
            val=fields[0]['value'] if fields else ''
            draft_preview=json.dumps(
                [dict(tieu_de=field['title'],van_ban=field['value']) for field in fields],
                ensure_ascii=False, indent=2,
            )
            browser_draft=json.dumps({
                json.dumps(field['path'],ensure_ascii=False): {
                    'title':field['title'],'path':field['path'],'value':field['value']}
                for field in fields
            },ensure_ascii=False)
            region_ids=list(s['regions'])
            selected_region=(s['selected_region_uid'] if s['selected_region_uid'] in region_ids
                             else (None if s.get('selection_cleared')
                                   else (region_ids[0] if region_ids else None)))
            region_box=s['regions'].get(selected_region,dict(bbox=[0,0,1,1],status='intact'))
            box_ids=list(s['bounding_boxes'])
            selected_box=(s['selected_box_id'] if s['selected_box_id'] in box_ids
                          else (box_ids[0] if box_ids else None))
            status_box=s['bounding_boxes'].get(selected_box,dict(status='intact'))
            status_choices=['intact','damaged']
            suspicious_ids=set(suspicious_box_ids(s))
            mismatch=source_mismatch_confirmed(s)
            final=(current_bundle['document'] if inspecting else
                   (final_source_mismatch_document(s) if mismatch else final_document(s)) if step==7 else None)
            suspicious_json={}
            source_mismatch_json=[]
            if step==7:
                suspicious_json={Path(image).stem: bundle['suspicious'] for image,bundle in {**ctx['baseline'], **ctx['committed']}.items() if bundle.get('suspicious')}
                if suspicious_ids:
                    suspicious_json=dict(suspicious_json)
                    suspicious_json[str(s['code'])]={
                        'issue_type':'suspicious_content',
                        'box_ids':sorted({int(box_id) for box_id in suspicious_ids}),
                        'note':SUSPICIOUS_NOTE,
                    }
                source_mismatch_json=[bundle['document'] for bundle in {**ctx['baseline'], **ctx['committed']}.values() if bundle['mismatch']]
                if mismatch and final:
                    current_code=str(s['code'])
                    source_mismatch_json=[
                        doc for doc in source_mismatch_json
                        if str(doc.get('inscription_code')) != current_code
                    ] + [final]
            issue=(s.get('source_mismatch') or {}) if mismatch else {}
            counts_differ=bool(has and len(s['regions']) != count_annotation_characters(s['annotation_text']))
            rendered=[browser_context(ctx),app_identity(s),gr.update(value=msg,visible=bool(msg)),
                    gr.update(visible=step==2 and has),gr.update(choices=choices,value=chosen),val,draft_preview,None,gr.update(value=snapshot(s),visible=step!=2),
                    gr.update(visible=step==3 and has),gr.update(choices=region_ids,value=selected_region),*region_box['bbox'],
                    gr.update(visible=step==4),gr.update(choices=box_ids,value=selected_box),
                    gr.update(choices=status_choices,value=('intact' if status_box['status']=='unknown' else status_box['status']),interactive=True),
                    None,json.dumps(s['reading_order']),gr.update(value=final),
                    gr.update(visible=step==6 and has),json.dumps(s.get('crop')),
                    gr.update(visible=step==7),
                    panel_heading(s),panel_summary(s),footer(s),gr.update(visible=step==2 and has),
                    gr.update(interactive=has and step>1),gr.update(interactive=has and step<7,visible=step<7),
                    status_rows(s),gr.update(visible=step==3 and has),
                    gr.update(value=issue.get('issue_type')),
                    gr.update(value=issue.get('note','')),
                    gr.update(interactive=has and step==3),gr.update(visible=mismatch),
                    gr.update(visible=has and step in (3,4)),
                    workflow_progress(s),LOADING_HIDDEN,
                    gr.update(visible=step==1),gr.update(visible=has and step>1 and not inspecting),
                    gr.update(visible=has and step>1 and not inspecting),gr.update(visible=has and step>1 and not inspecting),
                    (f'`{s["image"]}`' if has else '—'),
                    browser_draft,
                    gr.update(value=selected_box in suspicious_ids,
                              interactive=step==4 and selected_box is not None),
                    gr.update(value=source_text(s), visible=(step in (4, 7) and has)),
                    gr.update(visible=step==7),
                    gr.update(value=suspicious_json),
                    gr.update(visible=step==7 and bool(suspicious_json)),
                    gr.update(value=source_mismatch_json),
                    gr.update(visible=step==7 and bool(source_mismatch_json))]
            rendered.append(gr.update(interactive=True))
            rendered.append(gr.update(value=s.get('image_path') if step == 1 else None))
            if inspecting:
                for index in (3,8,9,15,21,23,27,31,36,46,47):
                    rendered[index]=gr.update(visible=False)
            rendered.extend([
                gr.update(visible=inspecting),
                gr.update(value=snapshot(s)) if inspecting else gr.skip(),
                content_markup(current_bundle) if inspecting else '',
                gr.update(visible=has and not inspecting),
                gr.update(interactive=has),
            ])
            return rendered

        def run(ctx, action, payload=None, auto_detect=True):
            ctx=with_baseline(ctx)
            active_before=ctx.get('active', {})
            log.info('action start action=%s image_path=%s image=%s step=%s revision=%s detection_loaded=%s content_verified=%s drafts=%s',
                     action,active_before.get('image_path'),active_before.get('image'),
                     active_before.get('current_step'),active_before.get('revision'),
                     active_before.get('detection_loaded'),
                     active_before.get('workflow',{}).get('content_verified'),
                     sorted(Path(key).name for key in ctx.get('drafts',{})))
            try:
                if action == 'save':
                    ctx=commit(ctx, engine.content_titles)
                    updated=ctx['active']
                    gr.Info('Annotation committed for this session. Download All to keep it.')
                else:
                    if action == 'back' and ctx['active']['current_step'] == 2:
                        ctx=deepcopy(ctx)
                        ctx['drafts'][ctx['active']['image_path']]=deepcopy(ctx['active'])
                    updated=engine.apply(ctx['active'],action,payload)
                    ctx=dict(ctx,active=updated)
                msg=''
                if auto_detect and action=='next' and updated['current_step']==3 and not updated['detection_loaded'] and not skip_detection:
                    try:
                        ctx=dict(ctx,active=engine.apply(updated,'detect'))
                    except Exception as exc:
                        log.exception('Detection failed')
                        msg='Detection failed: '+str(exc)
                result = render(ctx,msg)
                # Preserve unaffected editors and avoid replacing unrelated component values.
                suspicious_outputs={45}
                affected = {
                    'select': {0,2,8,10,11,12,13,14,16,17} | suspicious_outputs,
                    'suspicious': {0,2,8} | suspicious_outputs,
                    'status': {0,2,8,17,30},
                    'statuses': {0,2,8,17,30},
                    'reorder_text': {0,2,8,19} | suspicious_outputs,
                    'crop': {0,2,8,22},
                    'field': {0,2,6,7},
                }.get(action)
                if affected is not None:
                    always_extra=set(range(54,len(outputs)))
                    always={1,25,37,38}
                    result = [value if i in affected | always | always_extra else gr.skip() for i,value in enumerate(result)]
                return result
            except Exception as exc:
                log.exception('Action %s rejected',action)
                # Return a new revision even on errors, so the browser releases pending state.
                ctx=deepcopy(ctx);ctx['active']['revision']+=1
                return render(ctx,WARNING+' '+html.escape(str(exc)))

        def open_image(ctx,path):
            ctx=with_baseline(ctx)
            active=ctx.get('active',{})
            cached=path in ctx.get('drafts',{})
            log.info('open start requested_path=%s active_path=%s active_image=%s active_step=%s active_revision=%s cache_hit=%s cache_paths=%s',
                     path,active.get('image_path'),active.get('image'),
                     active.get('current_step'),active.get('revision'),cached,
                     sorted(Path(key).name for key in ctx.get('drafts',{})))
            try:
                if path not in {str(p.resolve()) for p in images}:raise ValueError('Select an image from the list.')
                ctx=deepcopy(ctx)
                old=ctx['active']
                # Step 1 is the image picker. Keep drafts for states that have
                # progressed into the annotation workflow, but do not overwrite
                # a valid cached draft with the transient picker state.
                if old.get('image_path') and old.get('mode') == 'fix' and old.get('current_step',1)>1:
                    ctx['drafts'][old['image_path']]=deepcopy(old)
                # Unsaved edits are kept per image in this Gradio session.
                bundle=ctx['committed'].get(Path(path).name, ctx['baseline'][Path(path).name])
                state=engine.open_image(path,bundle)
                state['revision']=old['revision']+1
                ctx['active']=state
                log.info('open success requested_path=%s resolved_image_path=%s image=%s step=%s revision=%s detection_loaded=%s content_verified=%s cache_hit=%s',
                         path,state.get('image_path'),state.get('image'),
                         state.get('current_step'),state.get('revision'),
                         state.get('detection_loaded'),
                         state.get('workflow',{}).get('content_verified'),cached)
                return render(ctx)
            except Exception as exc:
                log.exception('Cannot open image requested_path=%s active_path=%s active_step=%s',
                              path,active.get('image_path'),active.get('current_step'))
                return render(ctx,WARNING+' '+html.escape(str(exc)))

        # Hide Gradio's per-component timers/spinners and show one centered modal instead.
        event_args=dict(outputs=outputs,concurrency_id='annotation-actions',concurrency_limit=1,
                        show_progress='hidden',js=SHOW_LOADING_JS)
        box_color.change(
            fn=None, inputs=[box_color], outputs=None, show_progress='hidden',
            js="""(color) => {
                document.querySelector('#annotation-board')?.dispatchEvent(
                new CustomEvent('bbox-color-change', {detail: color, bubbles: true})
            );
            }""")
        hide_canvas_overlays.change(
            fn=None, inputs=[hide_canvas_overlays], outputs=None,
            show_progress='hidden',
            js="""(showImageOnly) => {
                document.querySelector('#annotation-board')?.dispatchEvent(
                    new CustomEvent('canvas-image-only-change', {
                        detail: Boolean(showImageOnly), bubbles: true
                    })
                );
            }""")
        def clear_loading_when_done(event):
            event.success(fn=None,inputs=None,outputs=None,js=HIDE_LOADING_JS)
            event.failure(fn=None,inputs=None,outputs=None,js=HIDE_LOADING_JS)
            return event

        def save_folder(ctx):
            return export_payload(with_baseline(ctx))

        def history_markup(ctx):
            report=build_summary(with_baseline(ctx))
            reviewable={image.name for image in images}
            entries=[entry for entry in report['images'] if entry['image'] in reviewable]
            items=[]
            for entry in entries:
                processed=entry['result'] != 'unreviewed'
                items.append(f'<li class="history-item {"processed" if processed else "pending"}"><span class="history-image-name">{html.escape(entry["image"])}</span><span class="history-status">{entry["result"].title()}</span></li>')
            reviewed=sum(entry['result'] != 'unreviewed' for entry in entries)
            return (f'<div class="history-count">Reviewed {reviewed}/{len(images)} images · Showing <span id="history-shown">{len(images)}</span></div><ul class="history-list">' + ''.join(items) + '<li class="history-empty" hidden>No matching images found.</li></ul>')

        history_open.click(
            fn=lambda ctx: (gr.update(visible=True), history_markup(ctx)),
            inputs=[session], outputs=[history_modal,history_results],
            js="(...args) => { document.querySelector('#history-modal')?.classList.remove('history-dismissed'); return args; }",
            show_progress='hidden').success(fn=None,inputs=[history_search,history_filter],outputs=None,
                                           show_progress='hidden',js=HISTORY_FILTER_JS)
        history_close.click(
            fn=lambda: gr.update(visible=False), inputs=[], outputs=[history_modal],
            js="(...args) => { document.querySelector('#history-modal')?.classList.add('history-dismissed'); return args; }",
            show_progress='hidden')
        history_filter.change(fn=None,inputs=[history_search,history_filter],outputs=None,
                              show_progress='hidden',js=HISTORY_FILTER_JS)
        history_search.input(fn=None,inputs=[history_search,history_filter],outputs=None,
                             show_progress='hidden',js=HISTORY_FILTER_JS)
        history_clear.click(fn=None,inputs=[history_filter],outputs=[history_search],
                            show_progress='hidden',js=HISTORY_CLEAR_JS)

        save_all.click(save_folder,[session],[download_payload],concurrency_id='annotation-actions',concurrency_limit=1,
                       show_progress='hidden',js=SHOW_LOADING_JS).success(
            fn=None,inputs=[download_payload],outputs=None,js="""(payload) => {
                document.getElementById('global-loading')?.classList.remove('is-visible');
                if (!payload) return;
                const archive=JSON.parse(payload);
                const binary=atob(archive.content);
                const bytes=new Uint8Array(binary.length);
                for (let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i);
                const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
                const link=document.createElement('a');link.href=url;link.download=archive.name;
                document.body.appendChild(link);link.click();link.remove();
                setTimeout(()=>URL.revokeObjectURL(url),10000);
            }""")
        clear_loading_when_done(open_button.click(open_image,[session,image_choice],**event_args))
        for button,action in [(save,'save'),(undo,'undo')]:
            clear_loading_when_done(button.click(lambda c,a=action:run(c,a),[session],**event_args))
        def start_fix(ctx, reset=False):
            ctx=deepcopy(with_baseline(ctx))
            current=ctx['active']
            path=current['image_path']
            image=current['image']
            if reset:
                state=engine.open_image(path,ctx['baseline'][image])
                ctx['drafts'].pop(path,None)
            else:
                state=deepcopy(ctx['drafts'].get(path,current))
            state['mode']='fix'
            if reset or path not in ctx['drafts']:
                state['current_step']=2
                state['workflow']['content_verified']=False
            state['revision']=current['revision']+1
            ctx['active']=state
            return render(ctx)

        def finish_image(ctx):
            return render(commit(with_baseline(ctx),engine.content_titles,finish=True))

        def return_picker(ctx):
            ctx=deepcopy(ctx)
            ctx['active']=new_state()
            return render(ctx)

        clear_loading_when_done(finish.click(finish_image,[session],**event_args))
        clear_loading_when_done(fix.click(start_fix,[session],**event_args))
        clear_loading_when_done(reset_all.click(lambda ctx:start_fix(ctx,True),[session],**event_args))
        clear_loading_when_done(inspection_picker.click(return_picker,[session],**event_args))

        def stage_for(ctx):
            state=ctx['active']
            if state.get('mode')=='inspect':return 'review'
            return {2:'content',3:'bounding_boxes',4:'status_and_order',5:'status_and_order',6:'crop',7:'review'}.get(state['current_step'],'review')

        def open_note(ctx):
            if not ctx['active'].get('image'):raise gr.Error('Select an image first.')
            stage=stage_for(ctx)
            return gr.update(visible=True),stage,ctx['notes'].get(ctx['active']['image'],{}).get(stage,''),ctx['active']['image']

        def save_note(ctx,stage,text,target_image=None):
            target_image=target_image or ctx['active'].get('image')
            if stage not in STAGES or target_image not in dataset:raise gr.Error('Invalid note image or stage.')
            ctx=deepcopy(ctx)
            notes=ctx['notes'].setdefault(target_image,{})
            if text.strip():notes[stage]=text
            else:notes.pop(stage,None)
            return browser_context(ctx),gr.update(visible=False)

        note_open.click(open_note,[session],[note_dialog,note_stage,note_text,note_image],concurrency_id='annotation-actions',concurrency_limit=1)
        note_save.click(save_note,[session,note_stage,note_text,note_image],[session,note_dialog],concurrency_id='annotation-actions',concurrency_limit=1)
        note_close.click(lambda:gr.update(visible=False),[],[note_dialog])

        def commit_frontend_content(ctx,draft):
            try:
                parsed=json.loads(draft or '{}')
                s=ctx['active']
                fields=content_fields(
                    s['draft_content'],s['code'],engine.content_titles,
                    engine.metadata_fields)
                expected={json.dumps(field['path'],ensure_ascii=False):field
                          for field in fields}
                if not isinstance(parsed,dict) or set(parsed) != set(expected):
                    raise ValueError('The local content draft is incomplete or invalid.')
                updated=s
                for key,field in expected.items():
                    entry=parsed[key]
                    if (not isinstance(entry,dict)
                            or entry.get('title') != field['title']
                            or entry.get('path') != list(field['path'])
                            or not isinstance(entry.get('value'),str)):
                        raise ValueError('The local content draft is invalid.')
                    if entry['value'] != field['value']:
                        updated=engine.apply(updated,'field',{
                            'path':entry['path'],'value':entry['value']})
                return dict(ctx,active=updated)
            except (ValueError,TypeError,AttributeError) as exc:
                raise ValueError(str(exc)) from exc

        clear_loading_when_done(back.click(lambda ctx,draft:run(commit_frontend_content(ctx,draft) if ctx['active']['current_step']==2 else ctx,'back'),[session,content_bridge],**event_args))
        def next_step(ctx, content_draft=None, auto_detect=True,
                      issue_type=None, mismatch_note_value='', selection='{}',
                      status_value='intact', x1_value=None, y1_value=None,
                      x2_value=None, y2_value=None, crop_coordinates=None):
            if ctx['active']['current_step'] == 2:
                try:
                    ctx=commit_frontend_content(ctx,content_draft)
                except Exception as exc:
                    result = render(ctx, WARNING+' '+html.escape(str(exc)))
                    result[4] = gr.skip()
                    result[5] = gr.skip()
                    return result
            if ctx['active']['current_step'] == 3:
                try:
                    ctx,_,_=commit_frontend_boxes(ctx, selection)
                except Exception as exc:
                    return render(ctx, WARNING+' '+html.escape(str(exc)))
            if (ctx['active']['current_step'] == 3
                    and not ctx['active']['workflow']['bbox_valid']
                    and not source_mismatch_confirmed(ctx['active'])
                    and issue_type):
                try:
                    updated = engine.apply(ctx['active'], 'confirm_source_mismatch', {
                        'issue_type': issue_type,
                        'note': mismatch_note_value or '',
                    })
                    ctx = dict(ctx, active=updated)
                except Exception as exc:
                    return render(ctx, WARNING+' '+html.escape(str(exc)))
            if ctx['active']['current_step'] == 4:
                try:
                    ctx = commit_statuses(ctx, selection, status_value)
                    text_sequence,token_order,suspicious_token_ids = frontend_text_sequence(selection)
                    if text_sequence is not None and (text_sequence or ctx['active']['annotations']):
                        updated = engine.apply(ctx['active'], 'reorder_text', {
                            'sequence': text_sequence,
                            'token_order': token_order,
                            'suspicious_token_ids': suspicious_token_ids,
                        })
                        ctx = dict(ctx, active=updated)
                    elif suspicious_token_ids is not None:
                        updated = engine.apply(ctx['active'], 'reorder_text', {
                            'sequence': list(ctx['active'].get('text_sequence', [])),
                            'token_order': list(map(str, ctx['active'].get('text_token_ids', []))),
                            'suspicious_token_ids': suspicious_token_ids,
                        })
                        ctx = dict(ctx, active=updated)
                    ctx = commit_statuses(ctx, selection, status_value)
                except Exception as exc:
                    return render(ctx, WARNING+' '+html.escape(str(exc)))
            if ctx['active']['current_step'] == 6:
                try:
                    crop_value = (sidebar_crop(crop_coordinates)
                                  if crop_coordinates not in (None,'')
                                  else frontend_crop(selection))
                    if crop_value is not None:
                        updated = engine.apply(ctx['active'], 'crop', {
                            'bbox': crop_value,
                        })
                        ctx = dict(ctx, active=updated)
                except Exception as exc:
                    return render(ctx, WARNING+' '+html.escape(str(exc)))
            result = run(ctx, 'next', auto_detect=auto_detect)
            if result[0]['active']['current_step'] == 2:
                # Keep the current section and unsaved input visible on failure.
                result[4] = gr.skip()
                result[5] = gr.skip()
            return result
        def next_with_progress(ctx, content_draft=None, issue_type=None,
                               mismatch_note_value='', selection='{}',
                               status_value='intact', x1_value=None, y1_value=None,
                               x2_value=None, y2_value=None, crop_coordinates=None):
            result = next_step(
                ctx, content_draft, auto_detect=False, issue_type=issue_type,
                mismatch_note_value=mismatch_note_value, selection=selection,
                status_value=status_value, x1_value=x1_value,
                y1_value=y1_value, x2_value=x2_value, y2_value=y2_value,
                crop_coordinates=crop_coordinates)
            state = result[0]['active']
            needs_detection = (state['current_step'] == 3 and
                               not state['detection_loaded'] and not skip_detection)
            if needs_detection:
                result[2] = gr.update(value='Running detection…', visible=True)
                result[29] = gr.update(interactive=False)
                result[28] = gr.update(interactive=False)
                result[38] = loading_markup('Running detection…', visible=True)
            yield result
            if needs_detection:
                yield run(result[0], 'detect')
        clear_loading_when_done(next_button.click(
            next_with_progress,
            [session,content_bridge,mismatch_type,mismatch_note,
             selection_bridge,status,x1,y1,x2,y2,crop_coords],
            **dict(event_args,js=snapshot_board_state_js(4))))
        field.change(
            fn=None,inputs=[field,content_bridge],outputs=[field_value],queue=False,
            js="""(path, draft) => {
                try { return JSON.parse(draft || '{}')?.[path]?.value ?? ''; }
                catch (_) { return ''; }
            }""",show_progress='hidden')
        apply_field.click(
            fn=None,inputs=[field,field_value,content_bridge],
            outputs=[content_bridge,content_preview],queue=False,
            js="""(path, value, draft) => {
                let fields={};
                try { fields=JSON.parse(draft || '{}'); } catch (_) {}
                if (fields[path]) fields[path].value=String(value ?? '');
                const preview=Object.values(fields).map(
                    field => ({tieu_de:field.title, van_ban:field.value}));
                return [JSON.stringify(fields), JSON.stringify(preview,null,2)];
            }""",show_progress='hidden')
        def frontend_selection(value):
            try:
                parsed=json.loads(value or '{}')
                active=parsed.get('active')
                selected=parsed.get('selected',[])
                if not isinstance(selected,list):raise ValueError
                return active,selected
            except (ValueError,TypeError,AttributeError):
                raise gr.Error('The local box selection is invalid.')
        def frontend_boxes(value):
            try:
                parsed=json.loads(value or '{}')
                if 'boxes' not in parsed:
                    return None
                boxes=parsed['boxes']
                if (not isinstance(boxes,dict)
                        or any(not isinstance(uid,str)
                               or not isinstance(box,(list,dict))
                               or not isinstance(box if isinstance(box,list) else box.get('bbox'),list)
                               or len(box if isinstance(box,list) else box['bbox']) != 4
                               for uid,box in boxes.items())):
                    raise ValueError
                return boxes
            except (ValueError,TypeError,AttributeError):
                raise gr.Error('The local bounding boxes are invalid.')
        def commit_frontend_boxes(ctx,selection,coordinates=(None,None,None,None),
                                  materialize_alignment=True,deselect=False):
            """Commit live canvas geometry plus the active sidebar coordinates."""
            boxes=frontend_boxes(selection)
            active,selected=frontend_selection(selection)
            if boxes is None:
                boxes={
                    uid:list(region['bbox'])
                    for uid,region in ctx['active']['regions'].items()
                }
            active=(active or ctx['active'].get('selected_region_uid')
                    or next(iter(ctx['active']['regions']),None))
            if any(coordinate is not None for coordinate in coordinates):
                # Gradio keeps the coordinate inputs mounted and may submit
                # their placeholder values even when there is no selected (or
                # even no existing) box.  That is not a manual coordinate edit
                # and must not block Next.
                if not active and not ctx['active']['regions']:
                    pass
                elif not active or any(coordinate is None for coordinate in coordinates):
                    raise ValueError('Select a box and enter all four coordinates.')
                else:
                    current=boxes.get(active)
                    boxes[active]=(dict(current,bbox=list(coordinates))
                                   if isinstance(current,dict) else list(coordinates))
                    if active not in selected:selected=[*selected,active]
            updated=engine.apply(ctx['active'],'commit_boxes',{
                'boxes':boxes,'active':active,'selected':selected,
                'materialize_alignment':materialize_alignment,
                'selection_cleared':deselect,
            })
            if deselect:
                updated['selected_region_uid']=None
                updated['selected_region_uids']=[]
                updated['selection_cleared']=True
            else:
                updated['selection_cleared']=False
            ctx=dict(ctx,active=updated)
            return ctx,active,selected
        def frontend_crop(value):
            try:
                parsed=json.loads(value or '{}')
                crop_value=parsed.get('crop')
                if crop_value is not None and (
                        not isinstance(crop_value,list) or len(crop_value) != 4):
                    raise ValueError
                return crop_value
            except (ValueError,TypeError,AttributeError):
                raise gr.Error('The local crop frame is invalid.')
        def sidebar_crop(value):
            try:
                parsed=json.loads(value) if isinstance(value,str) else value
                if (not isinstance(parsed,list) or len(parsed) != 4
                        or any(isinstance(item,bool) or not isinstance(item,(int,float))
                               for item in parsed)):
                    raise ValueError
                return parsed
            except (ValueError,TypeError):
                raise gr.Error('Crop coordinates must be [x1, y1, x2, y2].')
        def frontend_text_sequence(value):
            try:
                parsed=json.loads(value or '{}')
                sequence=parsed.get('textSequence')
                token_order=parsed.get('tokenOrder')
                suspicious_token_ids=parsed.get('suspiciousTokenIds')
                if sequence is not None and (
                        not isinstance(sequence,list)
                        or any(not isinstance(item,str) or not item for item in sequence)):
                    raise ValueError
                if token_order is not None and (
                        not isinstance(token_order,list)
                        or any(not isinstance(item,str) or not item for item in token_order)):
                    raise ValueError
                if suspicious_token_ids is not None and (
                        not isinstance(suspicious_token_ids,list)
                        or any(not isinstance(item,str) or not item
                               for item in suspicious_token_ids)):
                    raise ValueError
                return sequence,token_order,suspicious_token_ids
            except (ValueError,TypeError,AttributeError):
                raise gr.Error('The local text sequence is invalid.')
        def frontend_statuses(value):
            try:
                parsed=json.loads(value or '{}')
                statuses=parsed.get('statuses',{})
                unknowns=parsed.get('unknowns',{})
                if (not isinstance(statuses,dict)
                        or any(not isinstance(box_id,str)
                               or status not in ('intact','damaged')
                               for box_id,status in statuses.items())):
                    raise ValueError
                if (not isinstance(unknowns,dict)
                        or any(not isinstance(box_id,str) or not isinstance(val,bool)
                               for box_id,val in unknowns.items())):
                    raise ValueError
                return statuses, unknowns
            except (ValueError,TypeError,AttributeError):
                raise gr.Error('The local status selection is invalid.')
        def commit_statuses(ctx,selection,status_value='intact'):
            statuses={
                uid: box['status']
                for uid,box in ctx['active']['regions'].items()
            }
            unknowns_map={
                uid: bool(box.get('unknown', False))
                for uid,box in ctx['active']['regions'].items()
            }
            frontend, frontend_unknowns = frontend_statuses(selection)
            for box_id,status_name in frontend.items():
                region_uid=(ctx['active']['region_uid_by_box_id'].get(str(box_id)) or
                            (str(box_id) if str(box_id) in ctx['active']['regions'] else None))
                if region_uid and region_uid in statuses:
                    statuses[region_uid]=status_name
                    if box_id in frontend_unknowns:
                        unknowns_map[region_uid]=bool(frontend_unknowns[box_id]) if status_name == 'damaged' else False
            active,_=frontend_selection(selection)
            if active and str(active) not in frontend:
                region_uid=(ctx['active']['region_uid_by_box_id'].get(str(active)) or
                            (str(active) if str(active) in ctx['active']['regions'] else None))
                if region_uid and region_uid in statuses:
                    statuses[region_uid]=status_value
            updated=engine.apply(ctx['active'],'statuses',{'statuses':statuses, 'unknowns':unknowns_map})
            return dict(ctx,active=updated)
        apply_changes.click(
            fn=None,inputs=[],outputs=None,show_progress='hidden',
            js="""() => {
                document.querySelector('.workbench-board')?.dispatchEvent(
                    new CustomEvent('apply-status-preview', {bubbles: true}));
            }""")
        def update_coordinates(ctx,selection,a,b,d,e):
            active,_=frontend_selection(selection)
            if not active:raise gr.Error('Select a bounding box first.')
            return run(ctx,'update',dict(id=active,bbox=[a,b,d,e]))
        clear_loading_when_done(update.click(update_coordinates,
            [session,selection_bridge,x1,y1,x2,y2],**event_args))
        def delete_selected(ctx,selection,a,b,d,e):
            try:
                ctx,_,selected=commit_frontend_boxes(ctx,selection)
                if not selected:raise ValueError('Select at least one box to delete.')
                return run(ctx,'delete',dict(ids=selected))
            except Exception as exc:
                return render(ctx,WARNING+' '+html.escape(str(exc)))
        # Frontend draft state handles deletion instantly in editor.js
        def run_detection(ctx,confirmed):
            if ctx['active']['regions'] and not confirmed:
                return render(ctx)
            return run(ctx,'detect')
        clear_loading_when_done(detect.click(
            run_detection,[session,detect_confirm],
            **dict(event_args,js="""(ctx, confirmed) => {
                const hasBoxes=Object.keys(ctx?.active?.regions || {}).length > 0;
                const ok = !hasBoxes || window.confirm('Run detection again and replace all existing boxes?');
                if (ok) {
                    const el = document.getElementById('global-loading');
                    if (el) {
                        const label = el.querySelector('span:not(.global-loading-spinner)');
                        if (label) label.textContent = 'Running detection…';
                        el.classList.add('is-visible');
                    }
                }
                return [ctx, ok];
            }""")))
        def sort_current_boxes(ctx,selection,a,b,d,e):
            try:
                ctx,_,_=commit_frontend_boxes(ctx,selection)
                return run(ctx,'sort_boxes')
            except Exception as exc:
                return render(ctx,WARNING+' '+html.escape(str(exc)))
        # Frontend draft state & modal handle sort_boxes via sort_boxes_calc.
        def confirm_source_mismatch(ctx,issue_type,note,selection):
            try:
                # A mismatch confirmation belongs to an exact box count. Commit
                # the authoritative localBoxes snapshot before recording it.
                ctx,_,_=commit_frontend_boxes(
                    ctx,selection,materialize_alignment=False,deselect=True)
                return run(ctx,'confirm_source_mismatch',dict(
                    issue_type=issue_type,note=note))
            except Exception as exc:
                return render(ctx,WARNING+' '+html.escape(str(exc)))
        def clear_source_mismatch(ctx,selection):
            try:
                # Clearing mismatch metadata must not reload the older backend
                # box collection. Persist the exact localBoxes draft first.
                ctx,_,_=commit_frontend_boxes(
                    ctx,selection,materialize_alignment=False,deselect=True)
                result=run(ctx,'clear_source_mismatch')
                result[8]['value']['clearMismatchConfirmed'] = True
                return result
            except Exception as exc:
                return render(ctx,WARNING+' '+html.escape(str(exc)))
        clear_loading_when_done(confirm_mismatch.click(
            confirm_source_mismatch,
            [session,mismatch_type,mismatch_note,selection_bridge],
            **dict(event_args,js=snapshot_board_state_js(3,deselect=True))))
        reset_mismatch_ui.click(
            fn=None,inputs=[],outputs=[mismatch_type,mismatch_note],
            show_progress='hidden',js="() => [null, '']")
        clear_loading_when_done(clear_mismatch.click(
            clear_source_mismatch,[session,selection_bridge],
            **dict(event_args,js=snapshot_board_state_js(1,deselect=True))))
        for selector in (box_id,status_id):
            clear_loading_when_done(selector.input(lambda c,i:run(c,'select',dict(id=i)),[session,selector],**event_args))
        def on_action(ctx,evt:gr.EventData):
            if evt._data['action'] == 'sort_boxes_calc':
                payload=evt._data.get('payload') or {}
                request_id=payload.get('sortRequestId')

                def sort_response(board_state=None, ordered=None, message=''):
                    result=render(ctx,message)
                    board_value=snapshot(board_state or ctx['active'])
                    # Even a rejected sort must update the editor so its pending
                    # flag and progress surface are released. An empty result
                    # consumes any selected-sort range without changing orders.
                    board_value['calcSortedBoxIds']=ordered if ordered is not None else []
                    board_value['sortRequestId']=request_id
                    result[8]=gr.update(value=board_value,visible=True)
                    return result

                try:
                    raw=payload.get('boxes')
                    if not isinstance(raw,dict):
                        raise ValueError('Sort requires the current frontend box snapshot.')
                    transient=deepcopy(ctx['active'])
                    all_boxes=payload.get('allBoxes')
                    if not isinstance(all_boxes,dict):
                        raise ValueError('Sort requires the complete box state.')
                    transient['regions']={
                        str(uid):dict(box) for uid,box in all_boxes.items()
                        if isinstance(box,dict)
                    }
                    if len(transient['regions']) != len(all_boxes):
                        raise ValueError('The box state is invalid.')
                    refresh_bbox_validation(transient)
                    if not transient['workflow']['content_verified']:
                        raise ValueError('Verify the content before sorting boxes.')
                    # Check the submitted boxes against the recorded count and
                    # text. Order metadata does not affect mismatch approval.
                    if not (transient['workflow']['bbox_valid']
                            or source_mismatch_confirmed(transient)):
                        raise ValueError(
                            'Confirm the source mismatch before sorting when box and character counts differ.')
                    boxes={
                        str(uid):(box.get('bbox') if isinstance(box,dict) else box)
                        for uid,box in raw.items()
                    }
                    ordered=calculate_spatial_order(boxes,ctx['active']['image_size'])
                    if set(ordered) != set(boxes) or len(ordered) != len(boxes):
                        raise ValueError('Sorter did not return every frontend box exactly once.')
                    log.info('SORT: image=%s request_id=%s frontend count=%d sent IDs=%s returned IDs=%s',
                             ctx['active'].get('image_path'),payload.get('sortRequestId'),
                             len(boxes),list(boxes),ordered)
                    transient['selected_region_uids']=[
                        uid for uid in payload.get('selectedIds',[])
                        if uid in transient['regions']
                    ]
                    transient['selected_region_uid']=(
                        transient['selected_region_uids'][-1]
                        if transient['selected_region_uids'] else None)
                    return sort_response(transient,ordered)
                except Exception as exc:
                    log.exception('SORT rejected image=%s request_id=%s selected_ids=%s',
                                  ctx['active'].get('image_path'),request_id,
                                  payload.get('selectedIds'))
                    return sort_response(message=WARNING+' '+html.escape(str(exc)))
            return run(ctx,evt._data['action'],evt._data['payload'])
        board.action(on_action,[session],outputs=outputs,concurrency_id='annotation-actions',
                     concurrency_limit=1,show_progress='hidden')
        app.load(fn=lambda:browser_context(new_session(dataset)), inputs=None, outputs=[session], js=FONT_PRELOAD_JS,
                 show_progress='hidden')
    return app


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s: %(message)s')
    args=parser().parse_args()
    create_app(args).queue().launch(server_name=args.server_name,server_port=args.port,share=args.share,css=APP_CSS,
        theme=gr.themes.Base(font=['Arial', 'sans-serif'], font_mono=['monospace']), footer_links=[],
        allowed_paths=[str(Path(args.image_dir).resolve())])
