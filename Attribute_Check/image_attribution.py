"""
add_image_credits.py
--------------------
Enriches a cleaned POI JSON file with image attribution data.
For each object, looks up the image_url and adds an `image_credits`
property containing the source platform and the person to credit.

Supported sources (auto-detected from URL):
  - Wikimedia Commons  (no API key needed)
  - Flickr             (set FLICKR_API_KEY env var)
  - Unsplash           (set UNSPLASH_ACCESS_KEY env var)
  - Generic fallback   (EXIF/XMP metadata best-effort)

Usage:
    # First run — process entire file:
    python add_image_credits.py input.json output.json

    # Retry only failed entries in an already-processed file:
    python add_image_credits.py credited_data.json credited_data.json --retry-failed

    # With API keys:
    FLICKR_API_KEY=xxx python add_image_credits.py cleaned_data.json credited_data.json
"""

import sys
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FLICKR_API_KEY      = os.getenv("FLICKR_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

HEADERS = {"User-Agent": "ImageAttributionScript/1.0 (attribution lookup tool)"}

MAX_RETRIES  = 6          # attempts per entry (1 original + 4 retries)
RETRY_DELAY  = 3.0        # base delay in seconds (doubles each retry)
REQUEST_GAP  = 1       # polite gap between successful requests

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get(url: str, extra_headers: dict = None) -> Optional[dict]:
    """Fetch JSON with exponential backoff retry on failure."""
    req = urllib.request.Request(url, headers={**HEADERS, **(extra_headers or {})})
    delay = RETRY_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"      ↻ attempt {attempt} failed ({e}), retrying in {delay:.0f}s…", file=sys.stderr)
                time.sleep(delay)
                delay *= 2
            else:
                print(f"      ✗ all {MAX_RETRIES} attempts failed for: {url[:80]}", file=sys.stderr)
    return None


def http_get_bytes(url: str) -> Optional[bytes]:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(65536)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Wikimedia Commons
# ---------------------------------------------------------------------------

def is_wikimedia(url: str) -> bool:
    return "wikimedia.org" in url or "wikipedia.org" in url


def extract_wiki_filename(url: str) -> Optional[str]:
    thumb = re.search(r"/thumb/[0-9a-f]/[0-9a-f]{2}/([^/]+)/", url)
    if thumb:
        return urllib.parse.unquote(thumb.group(1))
    plain = re.search(r"/[0-9a-f]/[0-9a-f]{2}/([^/?#]+)$", url)
    if plain:
        return urllib.parse.unquote(plain.group(1))
    # Special:FilePath pattern
    fp = re.search(r"Special:FilePath/([^?&#]+)", url, re.IGNORECASE)
    if fp:
        return urllib.parse.unquote(fp.group(1))
    return None


def lookup_wikimedia(url: str) -> dict:
    filename = extract_wiki_filename(url)
    if not filename:
        return {"source": "Wikimedia Commons", "credit": None, "note": "Could not extract filename from URL"}

    api_url = (
        "https://commons.wikimedia.org/w/api.php"
        "?action=query&prop=imageinfo&iiprop=extmetadata|url"
        "&format=json"
        f"&titles=File:{urllib.parse.quote(filename)}"
    )
    data = http_get(api_url)
    if not data:
        return {"source": "Wikimedia Commons", "credit": None, "note": "API request failed"}

    pages = data.get("query", {}).get("pages", {})
    page  = next(iter(pages.values()), {})
    ii    = (page.get("imageinfo") or [{}])[0]
    meta  = ii.get("extmetadata", {})

    def gv(key):
        return meta.get(key, {}).get("value")

    artist_raw = gv("Artist") or ""
    artist = re.sub(r"<[^>]+>", "", artist_raw).strip() or None
    license_short = gv("LicenseShortName")

    return {
        "source": "Wikimedia Commons",
        "credit": artist,
        "license": license_short,
        "source_page": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(filename)}",
        "note": None if artist else "Author not found in metadata",
    }


# ---------------------------------------------------------------------------
# Flickr
# ---------------------------------------------------------------------------

FLICKR_LICENSE_MAP = {
    "0":  "All Rights Reserved",
    "1":  "CC BY-NC-SA 2.0",
    "2":  "CC BY-NC 2.0",
    "3":  "CC BY-NC-ND 2.0",
    "4":  "CC BY 2.0",
    "5":  "CC BY-SA 2.0",
    "6":  "CC BY-ND 2.0",
    "7":  "No known copyright",
    "8":  "United States Government Work",
    "9":  "CC0",
    "10": "Public Domain Mark",
}


def is_flickr(url: str) -> bool:
    return "flickr.com" in url or "staticflickr.com" in url


def extract_flickr_photo_id(url: str) -> Optional[str]:
    static = re.search(r"staticflickr\.com/\d+/(\d+)_", url)
    if static:
        return static.group(1)
    page = re.search(r"flickr\.com/photos/[^/]+/(\d+)", url)
    if page:
        return page.group(1)
    return None


def lookup_flickr(url: str) -> dict:
    if not FLICKR_API_KEY:
        return {"source": "Flickr", "credit": None, "note": "FLICKR_API_KEY not set"}

    photo_id = extract_flickr_photo_id(url)
    if not photo_id:
        return {"source": "Flickr", "credit": None, "note": "Could not extract photo ID from URL"}

    api_url = (
        f"https://api.flickr.com/services/rest/"
        f"?method=flickr.photos.getInfo&api_key={FLICKR_API_KEY}"
        f"&photo_id={photo_id}&format=json&nojsoncallback=1"
    )
    data = http_get(api_url)
    if not data or data.get("stat") != "ok":
        return {"source": "Flickr", "credit": None, "note": "API request failed or photo not found"}

    photo  = data["photo"]
    owner  = photo.get("owner", {})
    author = owner.get("realname") or owner.get("username")
    lic_id = str(photo.get("license", "0"))

    return {
        "source": "Flickr",
        "credit": author,
        "license": FLICKR_LICENSE_MAP.get(lic_id, "Unknown"),
        "source_page": f"https://www.flickr.com/photos/{owner.get('nsid', '')}/{photo_id}",
        "note": None,
    }


# ---------------------------------------------------------------------------
# Unsplash
# ---------------------------------------------------------------------------

def is_unsplash(url: str) -> bool:
    return "unsplash.com" in url


def extract_unsplash_photo_id(url: str) -> Optional[str]:
    m = re.search(r"photo-([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"unsplash\.com/photos/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def lookup_unsplash(url: str) -> dict:
    if not UNSPLASH_ACCESS_KEY:
        return {"source": "Unsplash", "credit": None, "note": "UNSPLASH_ACCESS_KEY not set"}

    photo_id = extract_unsplash_photo_id(url)
    if not photo_id:
        return {"source": "Unsplash", "credit": None, "note": "Could not extract photo ID from URL"}

    data = http_get(
        f"https://api.unsplash.com/photos/{photo_id}",
        extra_headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    )
    if not data:
        return {"source": "Unsplash", "credit": None, "note": "API request failed"}

    user = data.get("user", {})
    return {
        "source": "Unsplash",
        "credit": user.get("name"),
        "license": "Unsplash License",
        "source_page": data.get("links", {}).get("html"),
        "note": None,
    }


# ---------------------------------------------------------------------------
# Generic fallback (EXIF/XMP scan)
# ---------------------------------------------------------------------------

def lookup_generic(url: str) -> dict:
    raw = http_get_bytes(url)
    if not raw:
        return {"source": "Unknown", "credit": None, "note": "Could not fetch image data"}

    text = raw.decode("latin-1", errors="replace")

    def find_xmp(tag: str) -> Optional[str]:
        m = re.search(rf"<(?:dc:|xmpRights:|photoshop:)?{tag}[^>]*>([^<]{{1,200}})<", text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    creator = find_xmp("creator") or find_xmp("Artist")
    rights  = find_xmp("rights") or find_xmp("Copyright")

    if creator or rights:
        return {
            "source": "Embedded metadata",
            "credit": creator,
            "license": rights,
            "source_page": url,
            "note": "Extracted from EXIF/XMP",
        }

    return {
        "source": "Unknown",
        "credit": None,
        "source_page": url,
        "note": "No attribution found — check source manually",
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def get_image_credits(url: str) -> dict:
    url = url.strip()
    if is_wikimedia(url):
        return lookup_wikimedia(url)
    elif is_flickr(url):
        return lookup_flickr(url)
    elif is_unsplash(url):
        return lookup_unsplash(url)
    else:
        return lookup_generic(url)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def needs_retry(item: dict) -> bool:
    """Return True if this entry previously failed and should be retried."""
    credits = item.get("image_credits")
    if not isinstance(credits, dict):
        return False
    return credits.get("note") == "API request failed"


def build_credits(raw: dict) -> dict:
    """Normalise raw attribution dict into the final image_credits shape."""
    result = {
        "source":      raw.get("source"),
        "credit":      raw.get("credit"),
        "license":     raw.get("license"),
        "source_page": raw.get("source_page"),
    }
    if raw.get("note"):
        result["note"] = raw["note"]
    return result


def main():
    args        = sys.argv[1:]
    input_path  = args[0] if len(args) > 0 else "cleaned_data.json"
    output_path = args[1] if len(args) > 1 else "credited_data.json"
    retry_mode  = "--retry-failed" in args

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: JSON must be an array of objects.", file=sys.stderr)
        sys.exit(1)

    total       = len(data)
    results     = []
    retried     = 0
    newly_fixed = 0

    for i, item in enumerate(data):
        title = item.get("title", f"item {i+1}")
        url   = item.get("image_url", "").strip()


        if retry_mode and not needs_retry(item):
            results.append(item)
            continue

        if not url:
            print(f"[{i+1}/{total}] SKIP (no image_url): {title}", file=sys.stderr)
            results.append({**item, "image_credits": None})
            continue

        if retry_mode:
            retried += 1
            print(f"[{i+1}/{total}] RETRY: {title[:50]}", file=sys.stderr)
        else:
            print(f"[{i+1}/{total}] Looking up: {title[:50]}", file=sys.stderr)

        raw     = get_image_credits(url)
        credits = build_credits(raw)

        if retry_mode and credits.get("note") != "API request failed":
            newly_fixed += 1

        results.append({**item, "image_credits": credits})
        time.sleep(REQUEST_GAP)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    if retry_mode:
        still_failing = retried - newly_fixed
        print(f"\nRetry complete — {newly_fixed}/{retried} fixed, {still_failing} still failing.", file=sys.stderr)
    else:
        print(f"\nDone — wrote {len(results)} record(s) to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()