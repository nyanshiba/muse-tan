# Instagram Collection Media (embed, no login)

- `custom.instagram_media` fetches images for posts in an Instagram saved collection. It needs no account, no cookie, no API key and no browser.
- The helper is `~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py`. Its only inputs are the collection metadata file `~/workspace/instagram/muse-collection.json` (produced by `instagram-cli saved-posts`) and shortcodes derived from each item's permalink.
- Agreed scope (2026-11-14): one designated collection only; personal reference use; no constant patrol — runs happen on the user's word (e.g. when he says he added items) or inside a session he started. Originals of fetched images are never published, shared or sent anywhere.

## Route

- Anonymous `GET https://www.instagram.com/p/{shortcode}/embed/captioned/` (desktop UA, no cookies). Carousel posts carry every child's original-size `display_url` in the embedded JSON's `edge_sidecar_to_children`; single-image posts and reels carry one `EmbeddedMediaImage` (a poster frame for reels).
- Signed CDN URLs expire (measured ~4 days), so the helper downloads in the same run it parses.
- Requests are serialized over one `requests.Session`, reusing connections per host. On HTTP 429 or 403 the run stops immediately (exit 5) — no retry, no cooldown evasion.
- Instagram's terms prohibit automated collection; this route sits on the prohibited side and is used small-scale, on-demand, for personal reference only. Login-cookie routes are deliberately not used (checkpoint/suspension risk).

## Commands

Representative image (child 1, small) for every collection post with a permalink; already-saved files are skipped, so re-running after new items are added only fetches the new ones:

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py tops
```

All children (small) for specific posts, after Muse has shortlisted them from the representatives:

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py children <shortcode> [more...]
```

One post by permalink or shortcode (`--children` for all children):

```text
python3 ~/workspace/custom-connectors/custom.instagram_media/bin/instagram_media.py one <permalink-or-shortcode> [--children]
```

## Output

- Images: `~/workspace/instagram/media/<shortcode>/<index>-small.jpg` — long edge downscaled to 640px, JPEG q82 (measured 36–57 KB per file across carousel, single and reel-poster tests).
- Manifest: `~/workspace/instagram/media/manifest.json`, keyed by shortcode, with user, permalink, media_type, expected n_children, saved files and any count-mismatch note.
- A carousel whose parsed child count differs from the metadata `n_children` is noted in the manifest, not silently accepted. One collection item has no permalink and is out of this route's reach.

## Refresh flow (when nyanshiba adds items)

1. Re-run the collection metadata fetch (`fetch_collection.py` / `instagram-cli saved-posts`) to update `muse-collection.json`.
2. Run `tops` — new posts are fetched, existing representatives are skipped.
3. Muse reads the new representatives in batches and updates the want-to-wear list.

## Fallback (og:image)

- Some posts (observed: shisha, underwear-ad, Fantia/sister posts — sensitive or branded) serve the logged-out app shell instead of embed media. For those, `fetch_post` falls back to the post page's `og:image`: a 640x640 centre crop of the first image, saved as the representative with an `og-fallback ... provisional` note in the manifest. Treat those representatives as hold/undecided grade, never as a full read of the post. One post (CqIWx_JLduv, iyori_nu) had no og:image either and is unreachable anonymously.

## Verified

- 2026-11-14: carousel DfcKFJUEt6n (ceriina__, 7 children, media ids and child shortcodes match the earlier original-size verification exactly), single CI8MPDlHCpQ (gappex), reel poster C6SMF43vN85 (shaforostovadiana_). Small files were read back visually and matched the posts' actual images, including a child image (DfcKFJUEt6n child 4) whose content a caption-based reading had previously gotten wrong.
- 2026-11-14: first `tops` run over all 126 permalink items — 115 representatives via embed, 10 via og-fallback, 1 unreachable (above), no 429/403 encountered. Total media store 8.2 MB.
