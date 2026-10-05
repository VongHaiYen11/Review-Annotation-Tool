# Sino-Nôm Review Tool

Evaluate an existing SinoNom annotation export against the original images. The tool opens each image in a read-only Review canvas, then lets the evaluator accept it or correct it with the existing annotation editor.

## Install and run

Python 3.11 or newer is required.

```bash
python -m pip install -r requirements.txt
python gradio/app.py \
  --input-zip /path/to/annotations.zip \
  --image-dir /path/to/original/images \
  --skip-detection
```

The ZIP must contain `text_annotations.json` and/or `source_mismatches.json`. It may also contain `inscription_content.json` and `suspicious_details.json`. Files inside a parent directory in the ZIP are supported. The corresponding `review_` filenames are also supported, so reviewed submissions can be opened again. A previous summary is not imported: each new review session evaluates the supplied dataset as its baseline.

Original images must be directly inside the image folder, with unique filename stems. Records match their full `image` filenames. The picker and History show only images present in both the folder and the ZIP annotation records. Extra folder images are omitted; records whose images are missing are preserved unchanged in downloads and remain unreviewed in the summary. Available images are validated against their actual dimensions; missing images use declared source dimensions when present. Duplicate annotations, conflicting normal/mismatch records, and invalid geometry are rejected at startup. ZIP contents are read directly without extraction.

Use `--config configs/review.json` to change the annotation source heading. Content and metadata fields come from the exported content mapping, including extra headings. If source content is absent, Fix derives editable source text from the saved character assignments; inspection still reports that viewing content is unavailable.

## Review workflow

1. Select an image and click **Start Review**. The image canvas and content pane have equal heights and scroll independently. Crop/resize transforms, character labels, statuses, and suspicious markers come from the imported records. Zoom and pan remain available.
2. Click **Finish** to accept the displayed result, or **Fix** to enter Content → Bounding Boxes & Sort → Status & Order → Crop → Review.
3. In Content, **Save change** applies a field to the local draft. Continue with **Next** to verify the source text. Annotation source changes require a valid box/character alignment before final saving.
4. **Save Annotation** commits the completed correction for the session and returns to the picker. Entering Fix without net changes is classified as accepted.
5. Use **Download All** to keep the session's result.

**Reset all changes**, below the current fixing image's name, restores every annotation/content value from the imported baseline and returns to Content. It preserves evaluator notes. Reset affects the draft; Save Annotation must commit it before it replaces a previously saved correction.

The header **Note** button edits a note for the current stage. **Save note** includes it in the summary; Close discards unsaved note text. Saving an empty note removes that stage's note. Notes can exist for unreviewed images and do not themselves classify an image as fixed.

History distinguishes accepted, fixed, and unreviewed images. Drafts can be resumed during the same page session. Inspection shows the last committed result (or original), while Fix resumes any unfinished draft.

## Session lifetime

The imported ZIP is read-only. The browser owns the serialized dataset, per-image drafts, committed results, and notes in page memory. Python callbacks validate/process transient copies; no review result is stored in `gr.State`, an output directory, browser local storage, or a persistent database. Reloading or closing the page loses the review session.

Save Annotation and Save note only update session memory. **Download All is the only durable save.** Unfinished drafts never replace committed results in an export. Gradio/Pillow use temporary image previews for display; annotation records and ZIP exports are constructed in memory.

## Download format

The browser downloads `review_annotations.zip`, always containing:

- `review_text_annotations.json`
- `review_inscription_content.json`
- `review_source_mismatches.json`
- `review_suspicious_details.json`
- `review_summary.json`

The four data files contain the complete imported dataset with committed corrections applied. Unreviewed records keep their originals. Accepted unchanged records keep their originals. A corrected record replaces its original; unresolved drafts use the last committed version or original.

Resolving a source mismatch moves it into text annotations. Confirming a new mismatch moves it out of text annotations. Clearing suspicious markers updates the annotation's issue flags and removes the details entry when no suspicious boxes remain. Content corrections are committed alongside the annotation. Empty data files remain present as arrays or objects. Original image filenames are not prefixed.

## Evaluation summary

```json
{
  "counts": {"total": 2, "accepted": 0, "fixed": 1, "unreviewed": 1},
  "images": [
    {
      "image": "12305.jpg",
      "result": "fixed",
      "fixed_stages": ["status_and_order"],
      "changes": [
        {
          "stage": "status_and_order",
          "type": "character_changed",
          "original_box_id": "3",
          "final_box_id": "3",
          "before": "樂",
          "after": "楽"
        }
      ],
      "notes": {"status_and_order": "Corrected the final character."}
    },
    {
      "image": "12306.jpg",
      "result": "unreviewed",
      "fixed_stages": [],
      "changes": [],
      "notes": {}
    }
  ]
}
```

Changes compare the original imported baseline with the latest committed result. They describe content fields, box geometry, added/deleted boxes (including their characters), reading order, character assignment, status/unknown flags, crop/resize, source mismatches, and suspicious details. Region identity is retained during editing so renumbering a box does not falsely count as correcting its character. Reverted changes disappear from the summary.

Stage names are `content`, `bounding_boxes`, `status_and_order`, `crop`, and `review` (notes). Added boxes have a null original ID; deleted boxes have a null final ID. No evaluator identity, session ID, timestamps, or hashes are added.

## Optional detection

Manual review does not require machine-learning packages. To keep detection disabled, use `--skip-detection`. Otherwise, install an AutoHDR-compatible Torch/MMCV/MMDetection environment and place the existing model assets under:

```text
text_detection/models/ckpts/damage_detect.py
text_detection/models/ckpts/damage_detect.pth
text_detection/models/dists/det_model/det_model
```

Imported annotations never trigger automatic detection. The Fix workflow provides an explicit **Run detection** action to replace boxes. Model paths can be overridden with `--vague-det-config`, `--vague-det-weights`, and `--ocr-det-executable`.

## Demo and checks

```bash
python gradio/examples/review/create_demo.py --output-dir /tmp/sinonom-review-demo
python gradio/app.py --input-zip /tmp/sinonom-review-demo/input.zip --image-dir /tmp/sinonom-review-demo/images --skip-detection
python -m unittest discover -s gradio/tests
python -m unittest discover -s tests
```

PDF extraction, glyph-profile generation, and their utilities have been removed. `NomNaTong.ttf` and `DengXian.ttf` remain for Hán/Nôm display. Detection retains the existing AutoHDR-derived implementation.
