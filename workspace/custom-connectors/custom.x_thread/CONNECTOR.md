# X Thread (no browser)

- `custom.x_thread` reads public X (Twitter) posts through two unauthenticated HTTPS endpoints, the syndication tweet result and the public status page HTML. It needs no account, no API key and no browser.

- The helper is `~/workspace/custom-connectors/custom.x_thread/bin/x_thread.py`, a Python port of the user's own OpenCode plugin `x-thread` from `nyanshiba/harness-bonsai` (`.config/opencode/plugins/x-thread/`, commit 6e32b76, MIT). Its text digest format matches the original.

- Raw page HTML stays inside the helper. Only the assembled digest or JSON reaches the conversation.

## Connect

- There is nothing to connect. The endpoints are public and the connector descriptor sets `require_auth` to false.

- The syndication endpoint is unofficial and undocumented. X can change or retire it without notice, so treat a sudden failure as an upstream change before suspecting local setup.

## Credentials

- This connector uses no credential. Do not ask the user for an X login, cookie or API key for it.

- Requests carry only a desktop browser user agent string. Nothing about the user is sent.

## Fetch a post or thread

- Run the helper with a status URL or a bare post ID. Thread mode is the default and builds the whole author thread.

```text
python3 ~/workspace/custom-connectors/custom.x_thread/bin/x_thread.py "https://x.com/<user>/status/<id>"
```

- Accepted inputs are `x.com` and `twitter.com` status URLs (including `www.`, `mobile.`, `m.` and `/statuses/`), `fxtwitter.com` and `vxtwitter.com` mirror URLs, `publish.twitter.com` oembed URLs that wrap a status URL, and a bare numeric post ID.

- Transport is NAT-friendly, like the original. Requests are serialized and carried on one persistent keep-alive connection per host (one for the syndication host, one for x.com), through an explicit CONNECT tunnel when a proxy is configured. Measured 2026-11-10 by counting socket connect calls for a 5-post thread fetch: 24 connections with the previous per-request urllib transport, 3 with keep-alive (the designed pair plus one re-establishment). Thread and article outputs were byte-identical before and after the change.

- The digest starts with a header line `[x-thread] @<author> / <n> posts / root=<id> / start=<id>`, then the thread body numbered in order with t.co links already expanded. Quoted posts, other authors on the start path, and replies by others follow in their own marked sections.

- Add `--json` when a later step needs fields instead of prose. The JSON carries `posts`, `ancestors`, `replies`, `quoted`, `excluded` and a `diagnostics` object with `chain_ok`, `start_included`, `notes_completed` and `tco_remaining`.

```text
python3 ~/workspace/custom-connectors/custom.x_thread/bin/x_thread.py "https://x.com/<user>/status/<id>" --json
```

- Add `--mode post` for a single post only. It makes one syndication request, never fetches the page, and warns when the post is long form because the text may be truncated in this mode.

```text
python3 ~/workspace/custom-connectors/custom.x_thread/bin/x_thread.py "https://x.com/<user>/status/<id>" --mode post
```

- Add `--media` to also download photo attachments. Each photo is saved in its small variant under `~/workspace/x-thread-media/<post id>/`, the digest lists one `[media] photo <w>x<h> -> <path>` line per file under its post, and `--json` gives each post a `media` array whose entries carry the saved `path`, the original dimensions, the source `url` and a `medium_url` for a larger copy. The scope is the thread body, the start path and quoted posts, not replies; at most 20 files are downloaded per run, and an already-saved file is reused instead of downloaded again. A failed download degrades to a `[x-thread] notes:` line; in direct-network mode a media transport failure is exit 4, like any other direct failure. Videos and GIFs are not supported and return text only.

```text
python3 ~/workspace/custom-connectors/custom.x_thread/bin/x_thread.py "https://x.com/<user>/status/<id>" --media
```

## What the build does

- The walk goes up first, following reply parents in the syndication payload until the root, so starting from any post in a thread returns the whole thread.

- The walk then goes down. Candidate IDs are read from the newest post's page and each candidate is verified through syndication, so only the author's own self-reply chain joins the body and other people's replies land in the replies section.

- Note tweets get their full text back from the page HTML, with t.co links expanded from the page's embedded entity data.

- Article posts get their full body rebuilt from the page's embedded content blocks, in order, with headings kept. A code block in the article, such as a copy-and-paste prompt, is spliced in at the last embedded-media position when there is exactly one, and appended at the end when there are several. This article handling is an addition of this port and was not in the original plugin.

- Quoted posts are re-fetched through syndication and followed until no new quote appears.

- Set `X_THREAD_DEBUG=1` to append diagnostics to `/tmp/x-thread.log` and the rendered digest to `/tmp/x-thread-render.txt`. The paths can be changed with `X_THREAD_LOG` and `X_THREAD_RENDER`.

## Recover from errors

- Exit code 2 means the target was not a status URL or post ID. Fix the input instead of retrying.

- Exit code 3 means the start post was unavailable through syndication. The post may be deleted, protected, or the endpoint may have changed. Report that and fall back to the browser or `social.search` instead of retrying in a loop.

- An empty or short page result only disables the page-dependent parts. The thread body from syndication is still returned, so a digest with no down-chain and no note completion usually means the page fetch failed, not that the thread has no continuation. The digest flags this itself: a degraded build ends with `[x-thread] notes:` lines naming what may be missing, and `--json` carries the same signals as `diagnostics.start_page_ok` and `diagnostics.uncompleted_longform`.

- Keep request volume modest. Syndication calls run one at a time in order, and a single thread build is the intended unit of work. Do not bulk crawl timelines or poll with this helper.

## Direct-network mode (`--direct-network`) — measured notes, Hatch sandbox VM, 2026-11-10–11

Default path:

```
$ python3 bin/x_thread.py https://x.com/shibanyan_1/status/2082663972772479395
```

- Egress goes through the forward proxy `hatch-egress-proxy:3128` (from the `https_proxy` environment variable), CONNECT tunnel.
- TLS certificate presented for x.com on this path: issuer "Hatch Sandbox Egress CA". The connection is verified only as far as the gateway.
- Exit 0, digest printed.

Direct path:

```
$ python3 bin/x_thread.py https://x.com/shibanyan_1/status/2082663972772479395 --direct-network
```

- Proxy environment ignored. `x.com` and `cdn.syndication.twimg.com` taken from `bin/zone.hosts` pins; any other name resolved with DNS-over-HTTPS to Cloudflare, bootstrapped at the literal address `2606:4700:4700::1111` (SNI `cloudflare-dns.com`).
- TLS certificates: x.com — issuer Let's Encrypt; cdn.syndication.twimg.com — CN `*.twimg.com`, issuer Let's Encrypt.
- Exit 0. Output byte-identical to the default path for the same thread.
- On failure: exit 4, error on stderr, no fallback to the proxy (fail-closed).
- Measured without the permission (2026-11-11): the same command exits 4 in under a second — the connection lands on the gateway instead of the origin (SSL `WRONG_VERSION_NUMBER`). Exit 4 therefore also covers "permission missing"; that is the first thing to check.

### Environment facts behind the mode

- `/etc/hosts` is read-only in this VM (spawnd-managed, bind-mounted), so address pinning lives in `bin/zone.hosts` instead.
- Direct UDP/53 is refused inside the cell (EPERM); that is why resolution is DoH over TCP, not plain DNS.
- The mode requires the platform's direct-network access permission for "other TCP connections".
- Paired observation, recorded as-is: on 2026-11-07, a fetch of research.meta.ai on the default path answered HTTP 403. On 2026-11-11, the same article fetched directly (name resolved over DoH, TLS straight to the origin) answered 200, 200161 bytes, certificate CN `*.meta.ai` issued by DigiCert Inc.
- Not recommended by default: the mode steps outside the network path the platform provides. Whether to use it is the user's call.

### About `bin/zone.hosts`

- A plain-text file in hosts format (`IP hostname`, one per line). `directnet.py` reads it before any DNS lookup; a name found here never goes to DoH.
- It exists because `/etc/hosts` is read-only in the Hatch VM, and because some names need pins to be reachable over IPv6 at all: globally, `x.com`, `cdn.syndication.twimg.com` and `pbs.twimg.com` publish A records only, no AAAA.
- Published defaults: `x.com` is pinned to the Fastly anycast address `2a04:4e42::658`; `cdn.syndication.twimg.com` is pinned to `2a04:4e42::159`, the anycast address of its dual-stack Fastly target, which a hosts file cannot express as a CNAME; `pbs.twimg.com` is pinned to the same `2a04:4e42::159`. The pins are IPv6-only on purpose.
- Pins take precedence and are taken as-is: if an address goes stale, direct mode fails closed (exit 4) rather than falling back. Edit the file to local taste.
- Names not listed are resolved with DNS-over-HTTPS (see above).

Supporting files: `bin/directnet.py` (resolution and dialing), `bin/zone.hosts` (the pins, above).

## Limits

- Replies are limited to what the public conversation page exposes without a login. Some replies stay hidden and the count in the digest is a floor, not a total.

- The down walk stops after 12 hops and the up walk after 500 posts, matching the original.

- Note completion covers the thread body only. Notes quoted by the thread, and notes in replies, stay truncated, matching the original.

- Media downloads are opt-in (`--media`) and cover photos only, in the small variant. Videos and GIFs are not supported and return text only.

- Verification status, checked 2026-11-05 and updated 2026-11-08. Single post reads, the article build with its code block, reply separation, starting from a reply, JSON output and mirror URLs were all run live against real posts. The self-chain adoption in the down walk was covered by unit tests only until 2026-11-08, when it was verified live against a real long self-thread: @shibanyan_1's 5-post self-thread (root 2082663972772479395, posted 2026-07-30 to 2026-08-04). Runs starting from the root and from the last post returned the identical 5 posts in order, every post's parent matched the previous post, and diagnostics reported chain_ok and start_included true with tco_remaining 0; no self-reply was left in the replies section. Long-form completion was also run live on @hwchase17's "Your harness, your memory" (2042978500567609738, first fetched 2026-11-06 and re-run 2026-11-08): the full 9,137 characters were returned with note_completed true; in the helper's classification that post is article-type (is_article). The note_tweet-specific branch was then also verified live on 2026-11-08 against @Nikinik58681513's note post (2118596647467020576, is_note true, is_article false): syndication alone returned 206 truncated characters, thread mode completed the full 1,215 characters with note_completed true and tco_remaining 0.

- Deleted, protected or age-gated posts cannot be read this way.

## Related skills

- [Read X Posts and Threads](../../custom-skills/x_post_fetch/SKILL.md)

- Use `muse.skill_search` to find other skills and `muse.connector_search` to find other connectors.
