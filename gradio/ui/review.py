"""Read-only content pane for initial evaluation."""
import html


def content_markup(bundle):
    content = (bundle.get('content') or {}).get('content', {})
    sections = ''.join(
        f'<section><h3>{html.escape(title)}</h3><p>{html.escape(value)}</p></section>'
        for title, value in content.items() if isinstance(value, str))
    return '<div class="review-content-pane"><h2>Viewing content</h2>' + (
        sections or '<p>No viewing content is available for this image.</p>') + '</div>'
