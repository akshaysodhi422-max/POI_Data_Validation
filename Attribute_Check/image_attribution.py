"""
image_attribution.py
--------------------
Given a list of JSON objects with an image URL, enriches each object
with attribution/credit metadata.

Supported sources:
  - Wikimedia Commons  (via MediaWiki API — no key needed)
  - Flickr             (requires FLICKR_API_KEY env var)
  - Unsplash           (requires UNSPLASH_ACCESS_KEY env var)
  - Generic fallback   (EXIF + HTTP headers best-effort)

Usage:
    python image_attribution.py input.json output.json

    # or pipe directly:
    echo '[{"url": "https://upload.wikimedia.org/..."}]' | python image_attribution.py - -
"""

import sys
import json
import os
import re
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FLICKR_API_KEY   = os.getenv("FLICKR_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

HEADERS = {"User-Agent": "ImageAttributionScript/1.0 (attribution lookup tool)"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def http_get(url: str, extra_headers: dict = None) -> Optional[dict]:
    """Fetch JSON from a URL. Returns parsed dict or None on failure."""
    req = urllib.request.Request(url, headers={**HEADERS, **(extra_headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None


def http_get_bytes(url: str) -> Optional[bytes]:
    """Fetch raw bytes (for EXIF reading)."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(65536)  # read first 64 KB — enough for EXIF
    except Exception:
        return None


def empty_attribution() -> dict:
    return {
        "attribution_source": None,
        "author": None,
        "author_url": None,
        "license": None,
        "license_url": None,
        "title": None,
        "source_page": None,
        "attribution_note": None,
    }


# ---------------------------------------------------------------------------
# Wikimedia Commons
# ---------------------------------------------------------------------------

def is_wikimedia(url: str) -> bool:
    return "wikimedia.org" in url or "wikipedia.org" in url


def extract_wiki_filename(url: str) -> Optional[str]:
    """
    Extract the bare filename from a Wikimedia URL.
    Handles thumb URLs like:
      .../thumb/a/a3/Foo.jpg/1280px-Foo.jpg  -> Foo.jpg
      .../commons/a/a3/Foo.jpg               -> Foo.jpg
    """
    # thumb pattern: /thumb/xx/xx/FILENAME/NNNpx-FILENAME
    thumb = re.search(r"/thumb/[0-9a-f]/[0-9a-f]{2}/([^/]+)/", url)
    if thumb:
        return urllib.parse.unquote(thumb.group(1))

    # plain pattern: /commons/x/xx/FILENAME
    plain = re.search(r"/[0-9a-f]/[0-9a-f]{2}/([^/?#]+)$", url)
    if plain:
        return urllib.parse.unquote(plain.group(1))

    return None


def lookup_wikimedia(url: str) -> dict:
    attr = empty_attribution()
    attr["attribution_source"] = "Wikimedia Commons"

    filename = extract_wiki_filename(url)
    if not filename:
        attr["attribution_note"] = "Could not extract filename from URL"
        return attr

    api_url = (
        "https://commons.wikimedia.org/w/api.php"
        "?action=query"
        "&prop=imageinfo"
        "&iiprop=extmetadata|url"
        "&format=json"
        f"&titles=File:{urllib.parse.quote(filename)}"
    )

    data = http_get(api_url)
    if not data:
        attr["attribution_note"] = "Wikimedia API request failed"
        return attr

    pages = data.get("query", {}).get("pages", {})
    page  = next(iter(pages.values()), {})
    ii    = (page.get("imageinfo") or [{}])[0]
    meta  = ii.get("extmetadata", {})

    def gv(key):
        return meta.get(key, {}).get("value")

    # Artist field often contains HTML — strip tags
    artist_raw = gv("Artist") or ""
    artist = re.sub(r"<[^>]+>", "", artist_raw).strip() or None

    license_short = gv("LicenseShortName")
    license_url   = gv("LicenseUrl")
    title         = gv("ObjectName") or filename

    attr.update({
        "author":      artist,
        "license":     license_short,
        "license_url": license_url,
        "title":       title,
        "source_page": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(filename)}",
    })

    if not artist:
        attr["attribution_note"] = "Author metadata not found in API response"

    return attr


# ---------------------------------------------------------------------------
# Flickr
# ---------------------------------------------------------------------------

FLICKR_LICENSE_MAP = {
    "0": ("All Rights Reserved",        None),
    "1": ("CC BY-NC-SA 2.0",            "https://creativecommons.org/licenses/by-nc-sa/2.0/"),
    "2": ("CC BY-NC 2.0",               "https://creativecommons.org/licenses/by-nc/2.0/"),
    "3": ("CC BY-NC-ND 2.0",            "https://creativecommons.org/licenses/by-nc-nd/2.0/"),
    "4": ("CC BY 2.0",                  "https://creativecommons.org/licenses/by/2.0/"),
    "5": ("CC BY-SA 2.0",               "https://creativecommons.org/licenses/by-sa/2.0/"),
    "6": ("CC BY-ND 2.0",               "https://creativecommons.org/licenses/by-nd/2.0/"),
    "7": ("No known copyright",         "https://www.flickr.com/commons/usage/"),
    "8": ("United States Government",   None),
    "9": ("CC0",                        "https://creativecommons.org/publicdomain/zero/1.0/"),
    "10":("Public Domain Mark",         "https://creativecommons.org/publicdomain/mark/1.0/"),
}


def is_flickr(url: str) -> bool:
    return "flickr.com" in url or "staticflickr.com" in url or "live.staticflickr.com" in url


def extract_flickr_photo_id(url: str) -> Optional[str]:
    # Static URL: https://live.staticflickr.com/SERVERID/PHOTOID_xxxx.jpg
    static = re.search(r"staticflickr\.com/\d+/(\d+)_", url)
    if static:
        return static.group(1)
    # Page URL: https://www.flickr.com/photos/user/PHOTOID/
    page = re.search(r"flickr\.com/photos/[^/]+/(\d+)", url)
    if page:
        return page.group(1)
    return None


def lookup_flickr(url: str) -> dict:
    attr = empty_attribution()
    attr["attribution_source"] = "Flickr"

    if not FLICKR_API_KEY:
        attr["attribution_note"] = "FLICKR_API_KEY not set — cannot look up metadata"
        return attr

    photo_id = extract_flickr_photo_id(url)
    if not photo_id:
        attr["attribution_note"] = "Could not extract Flickr photo ID from URL"
        return attr

    api_url = (
        f"https://api.flickr.com/services/rest/"
        f"?method=flickr.photos.getInfo"
        f"&api_key={FLICKR_API_KEY}"
        f"&photo_id={photo_id}"
        f"&format=json&nojsoncallback=1"
    )

    data = http_get(api_url)
    if not data or data.get("stat") != "ok":
        attr["attribution_note"] = "Flickr API request failed or photo not found"
        return attr

    photo  = data["photo"]
    owner  = photo.get("owner", {})
    author = owner.get("realname") or owner.get("username")
    author_url = f"https://www.flickr.com/photos/{owner.get('nsid', '')}"
    title  = photo.get("title", {}).get("_content")
    lic_id = str(photo.get("license", "0"))
    lic_name, lic_url = FLICKR_LICENSE_MAP.get(lic_id, ("Unknown", None))

    attr.update({
        "author":      author,
        "author_url":  author_url,
        "license":     lic_name,
        "license_url": lic_url,
        "title":       title,
        "source_page": f"https://www.flickr.com/photos/{owner.get('nsid', '')}/{photo_id}",
    })
    return attr


# ---------------------------------------------------------------------------
# Unsplash
# ---------------------------------------------------------------------------

def is_unsplash(url: str) -> bool:
    return "unsplash.com" in url


def extract_unsplash_photo_id(url: str) -> Optional[str]:
    # https://images.unsplash.com/photo-PHOTOID?...
    m = re.search(r"photo-([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    # https://unsplash.com/photos/PHOTOID
    m = re.search(r"unsplash\.com/photos/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def lookup_unsplash(url: str) -> dict:
    attr = empty_attribution()
    attr["attribution_source"] = "Unsplash"

    if not UNSPLASH_ACCESS_KEY:
        attr["attribution_note"] = "UNSPLASH_ACCESS_KEY not set — cannot look up metadata"
        return attr

    photo_id = extract_unsplash_photo_id(url)
    if not photo_id:
        attr["attribution_note"] = "Could not extract Unsplash photo ID from URL"
        return attr

    api_url = f"https://api.unsplash.com/photos/{photo_id}"
    data = http_get(api_url, extra_headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"})

    if not data:
        attr["attribution_note"] = "Unsplash API request failed or photo not found"
        return attr

    user = data.get("user", {})
    author = user.get("name")
    author_url = user.get("links", {}).get("html")
    description = data.get("description") or data.get("alt_description")

    attr.update({
        "author":      author,
        "author_url":  author_url,
        "license":     "Unsplash License",
        "license_url": "https://unsplash.com/license",
        "title":       description,
        "source_page": data.get("links", {}).get("html"),
    })
    return attr


# ---------------------------------------------------------------------------
# Generic fallback — try EXIF
# ---------------------------------------------------------------------------

def lookup_generic(url: str) -> dict:
    attr = empty_attribution()
    attr["attribution_source"] = "Unknown"
    attr["source_page"] = url

    raw = http_get_bytes(url)
    if not raw:
        attr["attribution_note"] = "Could not fetch image data"
        return attr

    # Minimal EXIF/XMP scan — look for common credit fields in raw bytes
    text = raw.decode("latin-1", errors="replace")

    def find_xmp(tag: str) -> Optional[str]:
        pattern = rf"<(?:dc:|xmpRights:|photoshop:)?{tag}[^>]*>([^<]{{1,200}})<"
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    creator = find_xmp("creator") or find_xmp("Artist")
    rights  = find_xmp("rights") or find_xmp("Copyright")
    title   = find_xmp("title")

    if creator or rights or title:
        attr.update({
            "author":  creator,
            "license": rights,
            "title":   title,
            "attribution_note": "Extracted from embedded EXIF/XMP metadata",
        })
    else:
        attr["attribution_note"] = (
            "No attribution found. Check the source page manually: " + url
        )

    return attr


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def lookup_attribution(url: str) -> dict:
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

def process(items: list, url_key: str = "url") -> list:
    results = []
    for i, item in enumerate(items):
        url = item.get(url_key) or item.get("image") or item.get("img") or item.get("image_url")
        if not url:
            print(f"[{i+1}] Skipping — no URL field found in: {list(item.keys())}", file=sys.stderr)
            results.append({**item, **empty_attribution(), "attribution_note": "No URL field found"})
            continue

        print(f"[{i+1}] Looking up: {url[:80]}...", file=sys.stderr)
        attribution = lookup_attribution(url)
        results.append({**item, **attribution})

    return results


def main():
    # --- argument handling ---
    args = sys.argv[1:]
    input_path  = args[0] if len(args) > 0 else "-"
    output_path = args[1] if len(args) > 1 else "-"

    # Read input
    if input_path == "-":
        raw = sys.stdin.read()
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            raw = f.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input — {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        data = [data]  # wrap single object

    results = process(data)

    # Write output
    output = json.dumps(results, indent=2, ensure_ascii=False)
    if output_path == "-":
        print(output)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\nDone — wrote {len(results)} record(s) to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()