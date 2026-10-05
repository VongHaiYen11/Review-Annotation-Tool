"""Editable exported content fields; all operations are in memory."""
from copy import deepcopy
from pathlib import Path


def normalize_content_titles(titles):
    if not isinstance(titles, (list, tuple)) or not titles or any(not isinstance(t, str) or not t.strip() for t in titles):
        raise ValueError('Content headings must be non-empty strings.')
    if len(set(titles)) != len(titles):
        raise ValueError('Content headings must be unique.')
    return tuple(titles)


def normalize_metadata_fields(fields):
    return tuple(fields)


def content_fields(record, code, titles=None, metadata_fields=()):
    return [dict(title=title, value=value or '', path=('content', title))
            for title, value in record['content'].items()]


def content_document(image_name, code, record, titles=None, metadata_fields=()):
    return dict(image=Path(image_name).name, inscription_code=str(code), content=deepcopy(record['content']))


def validate_content_document(document, image_name, titles=None):
    if not isinstance(document, dict) or set(document) != {'image', 'inscription_code', 'content'}:
        raise ValueError('Invalid content document.')
    if document['image'] != Path(image_name).name or document['inscription_code'] != Path(image_name).stem:
        raise ValueError('Content does not belong to this image.')
    content = document['content']
    if (not isinstance(content, dict) or any(not isinstance(k,str) or not k.strip() for k in content)
            or any(v is not None and not isinstance(v,str) for v in content.values())):
        raise ValueError('Content values must be text or null, with non-empty headings.')
    return document


def edit_content_field(record, code, path, value, titles=None, metadata_fields=()):
    if list(path) not in [list(field['path']) for field in content_fields(record, code)] or not isinstance(value, str):
        raise ValueError('Invalid content field edit.')
    result = deepcopy(record)
    result['content'][path[1]] = value
    return result


def annotation_text(record, code, title):
    value = record['content'].get(title)
    if not isinstance(value,str):
        raise ValueError(f'Provide text for {title}.')
    return value
