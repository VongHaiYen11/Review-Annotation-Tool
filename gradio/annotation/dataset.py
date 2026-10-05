"""Read exported annotation ZIPs without extracting or modifying them."""
import json
import zipfile
from copy import deepcopy
from pathlib import Path

from PIL import Image

from .content import validate_content_document
from .io import _unique, validate_document, validate_source_mismatch_document

FILES = ('text_annotations', 'inscription_content', 'source_mismatches', 'suspicious_details')


def load_dataset(archive_path, images):
    paths = {p.name: p for p in map(Path, images)}
    payloads = {}
    with zipfile.ZipFile(archive_path) as archive:
        for name in FILES:
            matches = [info for info in archive.infolist()
                       if Path(info.filename).name in (name + '.json', 'Reviewed_' + name + '.json')]
            if len(matches) > 1:
                raise ValueError(f'Duplicate {name} files in ZIP.')
            if matches:
                if matches[0].file_size > 128 * 1024 * 1024:
                    raise ValueError(f'{name} is too large (maximum 128 MiB).')
                payloads[name] = json.loads(archive.read(matches[0]).decode('utf-8-sig'), object_pairs_hook=_unique)
            else:
                payloads[name] = {} if name == 'suspicious_details' else []
    result = {}
    for kind in ('text_annotations', 'source_mismatches'):
        if not isinstance(payloads[kind], list):
            raise ValueError(f'{kind} must be an array.')
        for document in payloads[kind]:
            if (not isinstance(document, dict) or not isinstance(document.get('image'), str)
                    or not document['image'] or Path(document['image']).name != document['image']):
                raise ValueError(f'{kind}: record must identify an image filename.')
            image = document['image']
            if image in result:
                raise ValueError(f'Duplicate or conflicting annotation for {image}.')
            if image in paths:
                with Image.open(paths[image]) as source:
                    size = list(source.size)
            elif 'image_resize' in document:
                from crop.crop import validate_resized_image_size
                resize = document['image_resize']
                size = validate_resized_image_size(resize.get('source_size') if isinstance(resize, dict) else None)
            else:
                # Coordinate-only Other records have no declared dimensions.
                # Validate finite, nonnegative geometry without image bounds.
                size = [float('inf'), float('inf')]
            if kind == 'source_mismatches':
                validate_source_mismatch_document(document, image, size)
            else:
                required = {'image', 'bounding_boxes', 'annotations', 'image_resize', 'crop'}
                if not required.issubset(document) or set(document) - (required | {'issue_type'}):
                    raise ValueError(f'Invalid annotation schema for {image}.')
                validate_document(document, image, size)
            result[image] = dict(document=deepcopy(document), mismatch=kind == 'source_mismatches',
                                 content=None, suspicious=None,
                                 origins={key: key for key in document['bounding_boxes']})
    if not result:
        raise ValueError('The ZIP contains no annotations or source mismatches.')
    content = payloads['inscription_content']
    if not isinstance(content, list):
        raise ValueError('inscription_content must be an array.')
    for document in content:
        if not isinstance(document, dict) or document.get('image') not in result:
            raise ValueError('Content references an image without an annotation.')
        image = document['image']
        validate_content_document(document, image)
        if result[image]['content'] is not None:
            raise ValueError(f'Duplicate content for {image}.')
        result[image]['content'] = deepcopy(document)
    suspicious = payloads['suspicious_details']
    if not isinstance(suspicious, dict):
        raise ValueError('suspicious_details must be an object.')
    codes = {Path(image).stem: image for image in result}
    for code, detail in suspicious.items():
        if code not in codes or not isinstance(detail, dict):
            raise ValueError(f'Invalid suspicious entry: {code}.')
        boxes = detail.get('box_ids')
        if (detail.get('issue_type') != 'suspicious_content'
                or not isinstance(detail.get('note'), str) or not isinstance(boxes, list)
                or any(type(key) is not int or str(key) not in result[codes[code]]['document']['bounding_boxes'] for key in boxes)
                or boxes != sorted(set(boxes))):
            raise ValueError(f'Invalid suspicious boxes for {code}.')
        result[codes[code]]['suspicious'] = deepcopy(detail)
    return result


def source_record(bundle, image, title):
    """Adapt exported flat content to the existing editor's field paths."""
    values = deepcopy(bundle['content']['content']) if bundle.get('content') else {}
    if not isinstance(values.get(title), str):
        doc = bundle['document']
        values[title] = doc.get('source_text', ''.join(
            doc.get('annotations', {})[key] for key in sorted(doc.get('annotations', {}), key=int)
            if doc['annotations'][key] != 'MISS'))
    return {'content': values}
