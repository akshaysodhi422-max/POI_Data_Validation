"""
clean_data.py
-------------
Removes all objects with image_review: false from the JSON array,
and strips the image_review property from every remaining object.

Usage:
    python clean_data.py repaired_data_part2.json cleaned_data_part2.json
"""

import json
import sys

input_path  = sys.argv[1] if len(sys.argv) > 1 else "repaired_data_part2.json"
output_path = sys.argv[2] if len(sys.argv) > 2 else "cleaned_data_part2.json"

with open(input_path, "r", encoding="utf-8") as f:
    data = json.load(f)

original_count = len(data)

cleaned = [
    {k: v for k, v in obj.items() if k != "image_review"}
    for obj in data
    if obj.get("image_review") is not False
]

removed = original_count - len(cleaned)

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(cleaned, f, indent=2, ensure_ascii=False)

print(f"Done: {original_count} → {len(cleaned)} objects ({removed} removed), saved to {output_path}")
