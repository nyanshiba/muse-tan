#!/usr/bin/env python3
"""Fetch small images for posts in an Instagram saved collection,
so Muse can read the images themselves instead of captions.

Route (verified 2026-11-14): anonymous GET of
https://www.instagram.com/p/{shortcode}/embed/captioned/ (no cookies),
parse the embedded JSON's edge_sidecar_to_children for carousel children
(original-size display_url), or the single EmbeddedMediaImage <img> for
single-image posts and reel posters. Signed CDN URLs expire (~4 days),
so downloads happen immediately in the same run.

Design rules (agreed 2026-11-14):
- Serialized requests over one requests.Session (connection reuse per host).
- Small only: images are downscaled to a 640px long edge and saved as
  ~/workspace/instagram/media/<shortcode>/<index>-small.jpg
- Already-saved files are never re-fetched.
- On HTTP 429 or 403 the run stops immediately (exit 5). No retries on those.
- Scope is the collection metadata file; nothing is written
  to Instagram and no credentials are used.

Usage:
  instagram_media.py tops [--limit N] [--offset N] [--delay SEC]
  instagram_media.py children SHORTCODE [SHORTCODE ...]
  instagram_media.py one PERMALINK_OR_SHORTCODE [--children]
"""
import argparse
import codecs
import html as html_mod
import io
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
from PIL import Image

HOME = os.path.expanduser("~")
COLLECTION = os.path.join(HOME, "workspace/instagram/muse-collection.json")
MEDIA_ROOT = os.path.join(HOME, "workspace/instagram/media")
MANIFEST = os.path.join(MEDIA_ROOT, "manifest.json")
SMALL_EDGE = 640
UA = "Mozilla/5.0"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US"}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RATE_LIMITED = 5


class RateLimited(Exception):
    def __init__(self, status, url):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


def shortcode_from_permalink(url):
    if not url:
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("p", "reel", "reels"):
        return parts[1]
    return None


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest):
    os.makedirs(MEDIA_ROOT, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def get(session, url, *, binary=False):
    """One GET with a single retry on transient network errors only."""
    last_exc = None
    for attempt in range(2):
        try:
            resp = session.get(url, headers=HEADERS, timeout=25)
        except requests.RequestException as exc:
            last_exc = exc
            continue
        if resp.status_code in (429, 403):
            raise RateLimited(resp.status_code, url)
        resp.raise_for_status()
        return resp.content if binary else resp.text
    raise last_exc


DISPLAY_URL_RE = re.compile(r'\\"display_url\\":\\"(https:[^"]+?)\\"')
FIELD_ID_RE = re.compile(r'\\"id\\":\\"(\d+)\\"')
FIELD_SC_RE = re.compile(r'\\"shortcode\\":\\"([^\\"]+)\\"')
FIELD_VIDEO_RE = re.compile(r'\\"is_video\\":(true|false)')
IMG_RE = re.compile(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"')


def decode_escapes(s):
    """The embed page nests its JSON inside JS strings, so URLs arrive
    multiply-escaped (slashes as \\\\/ sequences, & as \\u0026)."""
    s = s.replace("\\\\/", "/").replace("\\/", "/")
    s = s.replace("\\u0026", "&").replace("&amp;", "&")
    return s


def parse_children(page):
    """Return a list of {media_id, child_shortcode, is_video, url}.
    Carousel: the display_url fields inside the edge_sidecar_to_children
    segment, in document order (each node's own header precedes its
    display_url, so metadata is read from the chunk before it).
    Single/reel: the lone EmbeddedMediaImage (a poster frame for reels)."""
    idx = page.find("edge_sidecar_to_children")
    if idx >= 0:
        seg = page[idx:]
        children = []
        seen = set()
        prev_end = 0
        for m in DISPLAY_URL_RE.finditer(seg):
            url = decode_escapes(m.group(1))
            if url in seen:
                continue
            seen.add(url)
            chunk = seg[prev_end:m.start()]
            prev_end = m.end()
            ids = FIELD_ID_RE.findall(chunk)
            scs = FIELD_SC_RE.findall(chunk)
            vids = FIELD_VIDEO_RE.findall(chunk)
            children.append({
                "media_id": ids[-1] if ids else None,
                "child_shortcode": scs[-1] if scs else None,
                "is_video": (vids[-1] == "true") if vids else None,
                "url": url,
            })
        if children:
            return children
    m = IMG_RE.search(page)
    if m:
        return [{"media_id": None, "child_shortcode": None,
                 "is_video": None, "url": html_mod.unescape(m.group(1))}]
    return []


def save_small(data, dest):
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if max(img.size) > SMALL_EDGE:
        img.thumbnail((SMALL_EDGE, SMALL_EDGE), Image.LANCZOS)
    img.save(dest, "JPEG", quality=82)
    return img.size


def fetch_post(session, shortcode, *, want_children, expected_children=None):
    """Fetch one post. Returns (files, note). Raises RateLimited."""
    out_dir = os.path.join(MEDIA_ROOT, shortcode)
    os.makedirs(out_dir, exist_ok=True)
    rep = os.path.join(out_dir, "1-small.jpg")
    if not want_children and os.path.exists(rep) and os.path.getsize(rep) > 0:
        return ([{"index": 1, "file": rep, "skipped": True}], "already saved")
    page = get(session, f"https://www.instagram.com/p/{shortcode}/embed/captioned/")
    children = parse_children(page)
    if not children:
        # Some posts serve the logged-out app shell instead of embed media
        # (observed for sensitive / branded posts). Fall back to the post
        # page's og:image: a 640x640 centre crop of the first image. It is
        # a provisional representative only — the subject may be cut off.
        post_page = get(session, f"https://www.instagram.com/p/{shortcode}/")
        m = re.search(r'property="og:image" content="([^"]+)"', post_page)
        if not m:
            return ([], "no image found in embed page or post page")
        url = html_mod.unescape(m.group(1))
        dest = os.path.join(out_dir, "1-small.jpg")
        if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
            data = get(session, url, binary=True)
            save_small(data, dest)
        return ([{"index": 1, "file": dest, "og_fallback": True}],
                "og-fallback: embed served no media; square centre crop, provisional")
    targets = children if want_children else children[:1]
    files = []
    for i, child in enumerate(targets, start=1):
        dest = os.path.join(out_dir, f"{i}-small.jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            files.append({"index": i, "file": dest, "skipped": True,
                          "is_video": child["is_video"]})
            continue
        data = get(session, child["url"], binary=True)
        size = save_small(data, dest)
        files.append({"index": i, "file": dest, "size": list(size),
                      "is_video": child["is_video"]})
    note = ""
    if expected_children and len(children) != expected_children:
        note = f"children {len(children)} != metadata n_children {expected_children}"
    return (files, note)


def record(manifest, item, shortcode, files, note):
    entry = manifest.get(shortcode, {})
    entry.update({
        "user": item.get("user") if item else entry.get("user"),
        "permalink": item.get("permalink") if item else entry.get("permalink"),
        "media_type": item.get("media_type") if item else entry.get("media_type"),
        "n_children": item.get("n_children") if item else entry.get("n_children"),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M %Z"),
        "files": sorted({f["index"]: f for f in entry.get("files", []) + files}.values(),
                        key=lambda f: f["index"]),
        "note": note,
    })
    manifest[shortcode] = entry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_tops = sub.add_parser("tops", help="representative image for every collection post")
    p_tops.add_argument("--limit", type=int, default=None)
    p_tops.add_argument("--offset", type=int, default=0)
    p_tops.add_argument("--delay", type=float, default=0.7)
    p_children = sub.add_parser("children", help="all children (small) for given shortcodes")
    p_children.add_argument("shortcodes", nargs="+")
    p_one = sub.add_parser("one", help="one post by permalink or shortcode")
    p_one.add_argument("target")
    p_one.add_argument("--children", action="store_true")
    args = ap.parse_args(argv)

    session = requests.Session()
    manifest = load_manifest()
    done = failed = skipped_posts = 0

    def handle(item, shortcode, want_children):
        nonlocal done, failed
        files, note = fetch_post(
            session, shortcode, want_children=want_children,
            expected_children=item.get("n_children") if item else None)
        if files:
            record(manifest, item, shortcode, files, note)
            done += 1
            state = "skip" if all(f.get("skipped") for f in files) else "ok"
            print(f"[{state}] {shortcode} files={len(files)} {note}".rstrip())
        else:
            failed += 1
            print(f"[FAIL] {shortcode} {note}")

    try:
        if args.cmd == "tops":
            with open(COLLECTION, encoding="utf-8") as f:
                items = json.load(f)
            targets = []
            for item in items:
                sc = shortcode_from_permalink(item.get("permalink"))
                if sc:
                    targets.append((item, sc))
            targets = targets[args.offset:]
            if args.limit is not None:
                targets = targets[:args.limit]
            print(f"tops: {len(targets)} posts")
            for i, (item, sc) in enumerate(targets):
                rep = os.path.join(MEDIA_ROOT, sc, "1-small.jpg")
                if os.path.exists(rep) and os.path.getsize(rep) > 0:
                    skipped_posts += 1
                    continue
                handle(item, sc, want_children=False)
                save_manifest(manifest)
                if i < len(targets) - 1 and args.delay:
                    time.sleep(args.delay)
        elif args.cmd == "children":
            with open(COLLECTION, encoding="utf-8") as f:
                by_sc = {shortcode_from_permalink(i.get("permalink")): i
                         for i in json.load(f)}
            for sc in args.shortcodes:
                handle(by_sc.get(sc), sc, want_children=True)
                save_manifest(manifest)
        elif args.cmd == "one":
            sc = shortcode_from_permalink(args.target) or args.target.strip("/")
            handle(None, sc, want_children=args.children)
            save_manifest(manifest)
    except RateLimited as exc:
        save_manifest(manifest)
        print(f"STOP: rate limited ({exc}). done={done} failed={failed} "
              f"skipped={skipped_posts}", file=sys.stderr)
        return EXIT_RATE_LIMITED
    print(f"summary: done={done} failed={failed} skipped_posts={skipped_posts}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
