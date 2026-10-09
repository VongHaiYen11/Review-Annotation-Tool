"""Generate a small original-image folder and exported input ZIP for review."""
import argparse
import json
import sys
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crop.crop import crop_document, image_resize


def create_demo(output):
    output = Path(output)
    images = output / 'images'
    images.mkdir(parents=True, exist_ok=True)
    documents, content = [], []
    for number in (1, 2):
        name = f'{number}.png'
        image = Image.new('RGB', (600, 1800), '#f5e9d3')
        draw = ImageDraw.Draw(image)
        boxes = {}
        for box_id in range(1, 4):
            bbox = [420, 100 + 100 * box_id, 490, 170 + 100 * box_id]
            draw.rectangle(bbox, outline='#73604c', width=3)
            draw.text((bbox[0] + 25, bbox[1] + 25), str(box_id), fill='#3e3124')
            boxes[str(box_id)] = dict(bbox=bbox, status='intact', unknown=False,
                                     unavailable_font=False, expert_prediction=False, suspicious=False)
        image.save(images / name)
        documents.append(dict(image=name, bounding_boxes=boxes,
                              annotations={'1': '永', '2': '寺', '3': '樂'},
                              crop=crop_document(name, [20, 30, 580, 1770], list(image.size))['crop'],
                              image_resize=image_resize(list(image.size), list(image.size))))
        content.append(dict(image=name, inscription_code=str(number), content={
            'Nguyên văn chữ Hán Nôm': '永寺樂',
            'Tên bia': f'Demo {number}',
            'Toát yếu': '\n'.join(['Sample viewing content for independent scrolling.'] * 40)}))
    with zipfile.ZipFile(output / 'input.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, value in {'text_annotations.json': documents,
                                'inscription_content.json': content,
                                'source_mismatches.json': []}.items():
            archive.writestr(filename, json.dumps(value, ensure_ascii=False, indent=2))
    print(output / 'input.zip')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    create_demo(parser.parse_args().output_dir)
