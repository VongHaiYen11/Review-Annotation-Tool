# 漢 Sino-Nôm Review Tool

> A lightweight review interface for evaluating and correcting Sino-Nôm annotation exports against their original images.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![Gradio](https://img.shields.io/badge/Gradio-Review_UI-orange?logo=gradio&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Desktop-lightgrey)
![Status](https://img.shields.io/badge/Status-Active-success)

## ✨ Overview

**Sino-Nôm Review Tool** lets evaluators inspect existing annotations directly against the original images.

For each image, you can:

- 👁️ **Review** the imported annotation in a read-only canvas.
- ✅ **Accept** it without modification.
- ✏️ **Fix** incorrect content, bounding boxes, reading order, status, or crop.
- 📝 Add review notes for individual stages.
- 📦 Export the complete reviewed dataset and evaluation summary.

> [!NOTE] 
> Character detection, DINO damaged-character detection, and the reading-order sorting process are based on [AutoHDR](https://github.com/SCUT-DLVCLab/AutoHDR). Refer to that repository for the original models and sorting approach. Detection locates boxes; characters are assigned from the verified source text in Gradio. Review the proposed boxes and order before saving.
> This project's fusion adds one rule to AutoHDR: when a small damage box is contained in a larger OCR box with at least 80% smaller-box coverage and IoU below 0.5, the larger OCR box is retained as `damaged`. This avoids duplicate boxes for the same character.

---

## 🚀 Install & Run

Python **3.11+** is required.

```bash
python -m pip install -r requirements.txt
```

Run the review tool:

```bash
python gradio/app.py \
  --input-zip /path/to/annotations.zip \
  --image-dir /path/to/original/images \
  --skip-detection
```

### Input

The ZIP must contain:

- `text_annotations.json` and/or
- `source_mismatches.json`

Optional files:

- `inscription_content.json`

Reviewed files using the corresponding `review_` filenames are also supported, so previous review exports can be reopened.

Files may be located inside a parent directory within the ZIP.

> **Note:** Each new session treats the supplied dataset as its baseline. A previous `review_summary.json` is not imported.

### Original Images

Original images must be placed directly inside `--image-dir` and must have unique filename stems.

Only images available in **both** the image folder and annotation records appear in the review picker.

Extra images are ignored. Annotation records whose images are missing remain unchanged in the final export and are counted as **unreviewed**.

To customize the annotation source heading:

```bash
python gradio/app.py \
  --config configs/review.json \
  --input-zip /path/to/annotations.zip \
  --image-dir /path/to/original/images \
  --skip-detection
```

---

## 🔍 Review Workflow

### 1. Review

Select an image and click **Start Review**.

The review screen displays:

- original image and annotation overlay;
- character labels and reading order;
- status and suspicious markers;
- crop/resize transformations;
- source content.

Zoom and pan remain available.

### 2. Accept or Fix

Choose:

**Finish** → accept the current annotation.

**Fix** → open the correction workflow:

```text
Content
   ↓
Bounding Boxes & Sort
   ↓
Status & Order
   ↓
Crop
   ↓
Review
```

### 3. Edit

Changes remain in a local draft while editing.

In **Content**, use **Save change** to update a field, then **Next** to continue.

Annotation-source changes must have a valid box/character alignment before they can be saved.

### 4. Save

Click **Save Annotation** to commit the correction for the current session.

If **Fix** was opened but no actual changes were made, the image is classified as **accepted**.

### 5. Export

Click **Download All** to save the reviewed dataset.

> ⚠️ **Download All is the only durable save.**  
> Reloading or closing the page clears the current review session.

---

## ↩️ Reset Changes

**Reset all changes** restores the current image to its imported baseline and returns to the Content stage.

It resets annotation and content changes while preserving evaluator notes.

The reset only affects the current draft. Use **Save Annotation** to commit it.

---

## 📝 Review Notes

Use the **Note** button in the header to attach a note to the current stage.

- **Save note** stores the note in the session summary.
- **Close** discards unsaved text.
- Saving an empty note removes the existing note.

Notes can also be attached to unreviewed images and do not classify an image as fixed.

---

## 💾 Session & Persistence

The imported ZIP is **read-only**.

Review state is kept in browser page memory, including:

- drafts;
- committed corrections;
- review status;
- evaluator notes.

No review results are persisted to:

- `gr.State`;
- an output directory;
- browser local storage;
- a database.

**Save Annotation** and **Save note** update session memory only.

> 📦 Always use **Download All** before closing or reloading the page.

Unfinished drafts are never included as committed corrections in an export.

---

## 📦 Download Format

**Download All** creates:

```text
review_annotations.zip
├── text_annotations.json
├── inscription_content.json
├── source_mismatches.json
└── review_summary.json
```

The three annotation files contain the **complete imported dataset** with committed corrections applied.

| Record | Export behavior |
|---|---|
| ✅ Accepted | Original record preserved |
| ✏️ Fixed | Original replaced by correction |
| ⏳ Unreviewed | Original record preserved |
| 📝 Unfinished draft | Last committed version or original |

Resolved source mismatches move into text annotations. Newly confirmed mismatches move out of text annotations.

Suspicious markers and their detail entries are updated together.

---

## 📊 Evaluation Summary

`review_summary.json` records the result, changed stages, detailed changes, and notes for each image.

```json
{
  "counts": {
    "total": 2,
    "accepted": 0,
    "fixed": 1,
    "unreviewed": 1
  },
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
      "notes": {
        "status_and_order": "Corrected the final character."
      }
    }
  ]
}
```

Changes are calculated between the **original imported baseline** and the **latest committed result**.

Tracked changes include:

- content fields;
- bounding-box geometry;
- added/deleted boxes;
- character assignments;
- reading order;
- status and unknown flags;
- crop/resize;
- source mismatches;
- suspicious details.

Reverted changes disappear from the summary.

Stage identifiers are:

```text
content
bounding_boxes
status_and_order
crop
review
```

---

## 🤖 Optional Detection

Machine-learning dependencies are **not required** for manual review.

Keep detection disabled with:

```bash
--skip-detection
```

To enable detection, install an AutoHDR-compatible Torch/MMCV/MMDetection environment and provide:

```text
text_detection/models/
├── ckpts/
│   ├── damage_detect.py
│   └── damage_detect.pth
└── dists/
    └── det_model/
        └── det_model
```

Imported annotations **never trigger detection automatically**.

Detection runs only when the evaluator explicitly selects **Run detection** inside the Fix workflow.

Model paths can be overridden with:

```bash
--vague-det-config
--vague-det-weights
--ocr-det-executable
```

---

## 🧪 Demo & Tests

Create demo data:

```bash
python gradio/examples/review/create_demo.py \
  --output-dir /tmp/sinonom-review-demo
```

Run the demo:

```bash
python gradio/app.py \
  --input-zip /tmp/sinonom-review-demo/input.zip \
  --image-dir /tmp/sinonom-review-demo/images \
  --skip-detection
```

Run Gradio tests:

```bash
python -m unittest discover -s gradio/tests
```

Run project tests:

```bash
python -m unittest discover -s tests
```

---

## 🛠️ Technical Notes

- PDF extraction and glyph-profile utilities have been removed.
- `NomNaTong.ttf` and `DengXian.ttf` remain for Hán/Nôm rendering.
- Detection retains the existing AutoHDR-derived implementation.
- ZIP annotations are read directly without extraction.
- Annotation exports are constructed in memory.
Each annotated box requires boolean `unknown`, `unavailable_font`, `expert_prediction`, and `suspicious` flags. Suspicious belongs to the box and is mutually exclusive with Unknown. Legacy Suspicious sidecars are not imported. Download All always includes the three data files (empty groups use `[]`) and a regenerated `review_summary.json`; unsaved drafts are excluded.
