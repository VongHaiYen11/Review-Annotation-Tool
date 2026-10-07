"""Read-only content pane for initial evaluation."""
import html
from .text import display_annotation_text

CONTENT_CSS = '''
height: 100%; min-height: 0; overflow: hidden; display: flex; flex-direction: column;
.review-content-pane { flex: 1 1 0; min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 24px; box-sizing: border-box; }
.review-content-pane p { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--han-nom-font), Arial, sans-serif; line-height: 1.8; }
'''


def content_markup(bundle):
    content = (bundle.get('content') or {}).get('content', {})
    sections = ''.join(
        f'<section><h3>{html.escape(title)}</h3><p>{html.escape(display_annotation_text(value))}</p></section>'
        for title, value in content.items() if isinstance(value, str))
    return '<div class="review-content-pane"><h2>Viewing content</h2>' + (
        sections or '<p>No viewing content is available for this image.</p>') + '</div>'
