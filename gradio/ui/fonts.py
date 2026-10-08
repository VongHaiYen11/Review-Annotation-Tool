"""Bundled web-font fallback for Han/Nom text."""
import json
from pathlib import Path
from urllib.parse import quote

FONT_DIR = Path(__file__).resolve().parents[2] / 'fonts'
FONT_FORMATS = {'.ttf': 'truetype', '.otf': 'opentype',
                '.woff': 'woff', '.woff2': 'woff2', '.ttc': 'collection'}
# Preserve the established fallback order, then include every bundled font.
FONT_FILES = {}
for path in sorted(
        (path for path in FONT_DIR.rglob('*')
         if path.is_file() and path.suffix.lower() in FONT_FORMATS),
        key=lambda path: (0 if path.name == 'NomNaTong.ttf' else
                          1 if path.name == 'DengXian.ttf' else 2,
                          path.relative_to(FONT_DIR).as_posix())):
    family = f'Vietnamica {path.stem}'
    if family in FONT_FILES:
        family = f'Vietnamica {path.relative_to(FONT_DIR).as_posix()}'
    FONT_FILES[family] = path
# Limit these faces to CJK so Vietnamese/Latin labels keep the UI font.
CJK_RANGE = ('U+2E80-2FFF,U+3000-303F,U+3100-312F,U+31A0-31EF,'
             'U+3400-4DBF,U+4E00-9FFF,U+F900-FAFF,U+FE30-FE4F,'
             'U+16FE0-18DFF,U+20000-323AF')
DEFAULT_FONT_STACK = ', '.join(json.dumps(family, ensure_ascii=False) for family in FONT_FILES)
FONT_CSS = '\n'.join(
    f'''@font-face {{
      font-family: {json.dumps(family, ensure_ascii=False)};
      src: url("gradio_api/file={quote(str(path), safe='/')}") format("{FONT_FORMATS[path.suffix.lower()]}");
      font-weight: 400; font-style: normal; font-display: swap;
      unicode-range: {CJK_RANGE};
    }}'''
    for family, path in FONT_FILES.items()
)
FONT_CSS += f'\n.gradio-container {{ --han-nom-font: {DEFAULT_FONT_STACK}; }}\n'
