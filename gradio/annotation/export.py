"""Complete reviewed datasets, assembled entirely in memory."""
import base64
import io
import json
import zipfile
from copy import deepcopy
from .summary import build_summary


def export_documents(ctx):
    bundles={**ctx['baseline'], **ctx['committed']}
    return {
        'text_annotations.json': [deepcopy(b['document']) for b in bundles.values() if not b['mismatch']],
        'inscription_content.json': [deepcopy(b['content']) for b in bundles.values() if b.get('content') is not None],
        'source_mismatches.json': [deepcopy(b['document']) for b in bundles.values() if b['mismatch']],
        'review_summary.json': build_summary(ctx),
    }


def export_archive(ctx):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, document in export_documents(ctx).items():
            archive.writestr(filename,json.dumps(document or [],ensure_ascii=False,indent=2,allow_nan=False))
    return buffer.getvalue()


def export_payload(ctx):
    return json.dumps(dict(name='review_annotations.zip',content=base64.b64encode(export_archive(ctx)).decode('ascii')))
