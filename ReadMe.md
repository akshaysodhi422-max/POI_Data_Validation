# Data Review Project

A pipeline for collecting, reviewing, repairing, and cleaning POI (point of interest) data with image quality control.

---

## Pipeline Overview

```
Raw_Data  →  Data_Review  →  Data_Fix  →  Cleaned_Data
(raw json)   (review images)  (fix bad)    (final output)
```

---

## Folder Structure

### 📁 Raw_Data
Contains the unverified source data split across two JSON files:

- `merged_pois_part1.json` — first half of the raw POI dataset
- `merged_pois_part2.json` — second half of the raw POI dataset

Each object contains fields like `title`, `country`, `summary`, `image_url`, `lat`, `lon`, and `wikidata_id`. No images have been verified at this stage.

---

### 📁 Data_Review
Contains the image review tool.

**`image-review.html`** — A browser-based tool for manually reviewing images.

1. Drop in a raw JSON file (`merged_pois_part1.json` or `merged_pois_part2.json`)
2. Each POI is shown as a card with its image loaded
3. Mark each image as **Good ✓** or **Bad ✗**
4. Navigate page by page (5 cards at a time)
5. Export the result as **`reviewed_data.json`** — same structure as input but with an added `image_review` field (`true` = good, `false` = bad)

> Keyboard shortcuts: `1`–`5` to mark good, `Q`–`T` to mark bad, `←` `→` to navigate pages.

---

### 📁 Data_Fix
Contains the image repair tool.

**`image-repair.html`** — A browser-based tool for fixing rejected images.

1. Drop in a `reviewed_data.json` file
2. Only POIs with `image_review: false` are shown
3. For each rejected image, paste a new working image URL
4. Preview the new image before applying
5. Skip entries that can't be repaired
6. Export the result as **`repaired_data.json`** — fixed entries have their `image_url` updated and `image_review` set to `true`

> Keyboard shortcuts: `Enter` to apply URL, `S` to skip, `←` `→` to navigate pages.

---

### 📁 Cleaned_Data
Contains the final cleanup script.

**`clean_data.py`** — A Python script that strips out all remaining bad-image entries and removes the `image_review` field entirely, producing a clean final dataset.

**Usage:**
```bash
python clean_data.py repaired_data.json cleaned_data.json
```

**What it does:**
- Removes all objects where `image_review: false` (entries that were not fixable)
- Strips the `image_review` property from every remaining object
- Outputs **`cleaned_data.json`** — the final, clean dataset ready for use

---

## End-to-End Flow

| Step | Input | Tool | Output |
|------|-------|------|--------|
| 1. Review | `merged_pois_part1/2.json` | `image-review.html` | `reviewed_data.json` |
| 2. Repair | `reviewed_data.json` | `image-repair.html` | `repaired_data.json` |
| 3. Clean | `repaired_data.json` | `clean_data.py` | `cleaned_data.json` |