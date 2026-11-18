#!/usr/bin/env python3
"""Fetch saved-collection metadata via Muse's built-in Instagram connector CLI.

This is the UPSTREAM half of custom.instagram_media: it uses the logged-in
`instagram-cli saved-posts` command (part of Muse's built-in Instagram
connector, not of this repository) to list one saved collection and write
the metadata file that `instagram_media.py` consumes. The image fetching
itself is anonymous; only this metadata step needs a logged-in account.

Usage:
  python3 fetch_collection.py --account-id <id> --collection-id <id> \
      [--out ~/workspace/instagram/muse-collection.json] [--cli instagram-cli]

The account and collection ids are parameters, never hardcoded: they
identify the operator's own account and collection.
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account-id", required=True)
    ap.add_argument("--collection-id", required=True)
    ap.add_argument("--out", default=os.path.expanduser(
        "~/workspace/instagram/muse-collection.json"))
    ap.add_argument("--cli", default="instagram-cli",
                    help="path to the instagram-cli of Muse's built-in "
                         "Instagram connector")
    args = ap.parse_args()

    items = []
    seen = set()
    cursor = None
    pages = 0
    while True:
        cmd = [args.cli, "saved-posts", "--account-id", args.account_id,
               "--collection-id", args.collection_id]
        if cursor:
            cmd += ["--after", cursor]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("FAILED page", pages, r.stdout[:300], r.stderr[:300])
            sys.exit(1)
        d = json.loads(r.stdout)
        pages += 1
        new = 0
        for p in d.get("posts", []):
            pid = p.get("id")
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            cap = (p.get("caption") or {}).get("text") \
                if isinstance(p.get("caption"), dict) else p.get("caption")
            items.append({
                "id": pid,
                "user": (p.get("user") or {}).get("username"),
                "permalink": p.get("permalink"),
                "media_type": p.get("media_type"),
                "n_children": len(p.get("carousel_media") or []),
                "caption": (cap or "")[:200],
            })
        pi = d.get("page_info") or {}
        cur = pi.get("end_cursor")
        if not pi.get("has_next_page") or new == 0 or cur == cursor:
            print(f"pages={pages} items={len(items)} stop "
                  f"has_next={pi.get('has_next_page')} new={new}")
            break
        cursor = cur
        if pages > 20:
            print("page cap")
            break
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print("authors:", Counter(i["user"] for i in items).most_common(12))
    print("media_types:", Counter(i["media_type"] for i in items))


if __name__ == "__main__":
    main()
