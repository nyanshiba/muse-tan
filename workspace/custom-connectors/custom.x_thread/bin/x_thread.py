#!/usr/bin/env python3
"""x_thread.py: fetch an X (Twitter) post and its author thread without a browser.

Port of nyanshiba's OpenCode plugin `x-thread`
(harness-bonsai, .config/opencode/plugins/x-thread/index.ts, MIT) to a
standalone Muse helper. The engine is plain HTTPS only:

  - cdn.syndication.twimg.com/tweet-result for post bodies (no auth)
  - x.com status pages (unauthenticated HTML) only as an ID index and as
    the source for note-tweet full text. Raw HTML never leaves this script.

Features (same numbering as the original):
  1. Whole tree: walk up via in_reply_to_status_id_str to the root, then
     extend down along the author's self-reply chain.
  2. Replies by others, separated from the thread body.
  3. Quoted posts, followed until no new quote appears.
  4. Note tweets: full text recovered from the page HTML, t.co expanded.

Addition in this port: with --media, photo attachments are downloaded
(small variant) from pbs.twimg.com to ~/workspace/x-thread-media/<post id>/
and their paths are listed in the output. Videos and GIFs are not
supported.

Usage:
  x_thread.py <status-url | post-id> [--json] [--mode thread|post] [--media]
  x_thread.py https://x.com/eris_create_lab/status/1985660000000000000

Exit codes: 0 ok, 2 bad target, 3 start post unavailable, 4 fetch failed.
Debug: X_THREAD_DEBUG=1 appends diagnostics to $X_THREAD_LOG
(default /tmp/x-thread.log) and the rendered digest to $X_THREAD_RENDER
(default /tmp/x-thread-render.txt).
"""

import argparse
import base64
import html as htmlmod
import http.client
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CDN_HOST = "cdn.syndication.twimg.com"
MAX_HOPS = 12
PAGE_ATTEMPTS = 4
SYND_TIMEOUT = 10
PAGE_TIMEOUT = 20

STATUS_URL = re.compile(
    r"https?://(?:www\.|mobile\.|m\.)?(?:x|twitter)\.com/([^/]+)/status(?:es)?/(\d+)"
)
MIRROR_HOST = re.compile(r"^(?:api\.)?(?:fxtwitter|vxtwitter)\.com$")
REST_ID_RE = re.compile(r'\\?"rest_id\\?":\\?"(\d{19})\\?"')
CONVERSATION_RE = re.compile(r"conversationthread-(\d{19})")
FALLBACK_ID_RE = re.compile(r"\b2\d{18}\b")
TEXT_RE = re.compile(r'"text":"((?:[^"\\]|\\.)*)"')
EXPANDED_RE = re.compile(r'"expanded_url":"((?:[^"\\]|\\.)*)"[^{}]{0,240}?"url":"((?:[^"\\]|\\.)*)"')


def dlog(line: str) -> None:
    if os.environ.get("X_THREAD_DEBUG") != "1":
        return
    path = os.environ.get("X_THREAD_LOG", "/tmp/x-thread.log")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError:
        pass


def id_cmp_key(post_id: str):
    # Snowflake IDs exceed float/int53 safety in some languages. Python ints
    # are exact, but keep the original length-then-lexicographic rule so the
    # ordering matches the reference implementation bit for bit.
    return (len(post_id), post_id)


def status_target(url: str):
    """Return (author, start_id) for direct, mirror and oembed URLs."""
    m = STATUS_URL.search(url)
    if m:
        return m.group(1), m.group(2)
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.hostname and MIRROR_HOST.match(u.hostname):
        m = re.match(r"^/([^/]+)/status(?:es)?/(\d+)", u.path)
        if m:
            return m.group(1), m.group(2)
        return None
    if u.hostname == "publish.twitter.com":
        inner = parse_qs(u.query).get("url", [None])[0]
        if inner:
            m = STATUS_URL.search(inner)
            if m:
                return m.group(1), m.group(2)
    if re.fullmatch(r"\d{5,25}", url.strip()):
        return None, url.strip()
    return None

# --- transport ---------------------------------------------------------------
# The original chains its syndication calls over a single keep-alive
# connection, and the pages arrive over the host fetch, so a whole thread
# costs about two TCP connections (NAT-friendly by design). urllib has no
# connection pool (every urlopen is a fresh TCP+TLS session), so this port
# keeps one http.client connection per host instead: one persistent
# connection for the syndication host, one for x.com. Calls stay
# serialized, one at a time, in order, as in the original.
#
# The environment's HTTPS proxy is honored with an explicit CONNECT
# tunnel. A plain direct TLS connection from some sandboxes fails, so
# the tunnel setup is deliberate; with no proxy configured the same code
# connects directly.
#
# With --direct-network, the transport swaps to the directnet module
# instead: names are resolved from a local zone file or DNS-over-HTTPS
# and connections go straight to the origin, ignoring the proxy
# environment entirely. That mode is fail-closed. A direct failure is
# reported (exit code 4) and nothing falls back to the proxy.

DIRECT_NETWORK = False
_dn = None


def _directnet():
    global _dn
    if _dn is None:
        import importlib
        import importlib.util

        try:
            _dn = importlib.import_module("directnet")
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "directnet",
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "directnet.py"
                ),
            )
            _dn = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_dn)
    return _dn


class KeepAliveHTTPS:
    """One reusable HTTPS connection to a single host, proxy-aware."""

    def __init__(self, host: str, timeout: float):
        self.host = host
        self.timeout = timeout
        self.conn = None

    def _open(self):
        if DIRECT_NETWORK:
            conn = http.client.HTTPConnection(self.host, timeout=self.timeout)
            conn.sock = _directnet().open_tls(self.host, 443, self.timeout)
            dlog(f"connect {self.host} (direct)")
            self.conn = conn
            return
        proxy = None
        if not urllib.request.proxy_bypass(self.host):
            proxies = urllib.request.getproxies()
            proxy = proxies.get("https") or proxies.get("http")
        if proxy:
            pu = urlparse(proxy if "://" in proxy else f"http://{proxy}")
            conn = http.client.HTTPSConnection(
                pu.hostname, pu.port or 80, timeout=self.timeout
            )
            headers = {}
            if pu.username:
                raw = f"{pu.username}:{pu.password or ''}".encode()
                headers["Proxy-Authorization"] = (
                    "Basic " + base64.b64encode(raw).decode()
                )
            conn.set_tunnel(self.host, 443, headers=headers)
        else:
            conn = http.client.HTTPSConnection(
                self.host, 443, timeout=self.timeout
            )
        conn.connect()
        dlog(f"connect {self.host}")
        self.conn = conn

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def get(self, path: str, headers: dict):
        """GET path on this host. Return (status, headers, body bytes).

        Same-host redirects are followed (up to 5), matching the old
        urllib behaviour. Raises on transport failure after one retry
        on a fresh connection, which also covers stale keep-alive
        connections dropped by the server.
        """
        for _hop in range(6):
            status, resp_headers, body = self._get_once(path, headers)
            location = resp_headers.get("location") if resp_headers else None
            if status not in (301, 302, 303, 307, 308) or not location:
                return status, resp_headers, body
            lu = urlparse(location)
            if lu.hostname and lu.hostname != self.host:
                return status, resp_headers, body
            path = lu.path or "/"
            if lu.query:
                path += "?" + lu.query
        return status, resp_headers, body

    def _get_once(self, path: str, headers: dict):
        for attempt in range(2):
            try:
                if self.conn is None:
                    self._open()
                self.conn.request("GET", path, headers=headers)
                res = self.conn.getresponse()
                body = res.read()
                out = (res.status, res.headers, body)
                if res.will_close:
                    self.close()
                return out
            except Exception:
                self.close()
                if attempt == 1:
                    raise
        raise RuntimeError("unreachable")


SYND_CLIENT = KeepAliveHTTPS(CDN_HOST, SYND_TIMEOUT)
PAGE_CLIENT = KeepAliveHTTPS("x.com", PAGE_TIMEOUT)
MEDIA_CLIENT = KeepAliveHTTPS("pbs.twimg.com", 60)

MEDIA_ENABLED = False
MEDIA_DIR = os.path.expanduser("~/workspace/x-thread-media")
MEDIA_VARIANT = "small"
MEDIA_MAX_FILES = 20
MEDIA_MAX_BYTES = 20 * 1024 * 1024


class Syndication:
    def fetch(self, post_id: str):
        """Return the tweet JSON dict, {'__status': ...} for HTTP errors,
        or None for transport/parse failures."""
        path = f"/tweet-result?id={post_id}&lang=en&token=0"
        try:
            status, _headers, body = SYND_CLIENT.get(
                path,
                headers={"user-agent": USER_AGENT, "accept": "application/json"},
            )
        except Exception as e:
            if DIRECT_NETWORK:
                raise _directnet().DirectError(
                    f"direct: syndication fetch failed for {post_id}: {e}"
                )
            return None
        if status != 200:
            return {"__status": status}
        text = body.decode("utf-8", "replace")
        if len(text) <= 2:
            return {"__status": "empty"}
        try:
            data = json.loads(text)
        except Exception:
            return None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("id_str"), str)
            or not data["id_str"].isdigit()
        ):
            return None
        return data


SYND = Syndication()


def fetch_page(post_id: str, author: str) -> str:
    """Fetch the unauthenticated x.com status page HTML, or '' on failure."""
    path = f"/{author}/status/{post_id}"
    try:
        status, headers, body = PAGE_CLIENT.get(
            path,
            headers={
                "user-agent": USER_AGENT,
                "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "accept-language": "en",
            },
        )
    except Exception as e:
        if DIRECT_NETWORK:
            raise _directnet().DirectError(
                f"direct: page fetch failed for {post_id}: {e}"
            )
        return ""
    if status != 200:
        return ""
    ct = (headers.get("content-type") or "").lower()
    if ct and "html" not in ct:
        return ""
    return body.decode("utf-8", "replace")


# --- payload shaping ---------------------------------------------------------


def links_of(j) -> list:
    out = []
    entities = (j or {}).get("entities") or {}
    for e in list(entities.get("urls") or []) + list(entities.get("media") or []):
        if not isinstance(e, dict):
            continue
        expanded = e.get("expanded_url") or e.get("media_url_https")
        if e.get("url") and expanded:
            out.append({"tco": e["url"], "url": expanded})
    return out


def photos_of(j) -> list:
    """Photo attachments of a syndication payload, as download descriptors.

    Only type "photo" entries are returned. Videos and animated GIFs are
    dropped here, at the shaping boundary, so they never reach the media
    download or the output at all: videos and GIFs are not supported.
    """
    out = []
    for m in (j or {}).get("mediaDetails") or []:
        if not isinstance(m, dict) or m.get("type") != "photo":
            continue
        url = m.get("media_url_https")
        if not url:
            continue
        info = m.get("original_info") or {}
        out.append(
            {"url": url, "width": info.get("width"), "height": info.get("height")}
        )
    return out


def expand_links(text: str, links: list) -> str:
    out = text
    for link in sorted(links, key=lambda l: -len(l["tco"])):
        if link["tco"] in out:
            out = out.replace(link["tco"], link["url"])
    return out


def to_tweet(j) -> dict:
    quoted = (j or {}).get("quoted_tweet") or {}
    quotes = []
    if quoted.get("id_str"):
        quotes.append(
            {
                "id": str(quoted["id_str"]),
                "author": (quoted.get("user") or {}).get("screen_name") or "?",
                "text": quoted.get("text") or "",
                "links": links_of(quoted),
                "media": photos_of(quoted),
            }
        )
    article = (j or {}).get("article") or {}
    return {
        "id": j["id_str"],
        "author": (j.get("user") or {}).get("screen_name") or "?",
        "created_at": j.get("created_at"),
        "text": j.get("text") or "",
        "is_note": "note_tweet" in j,
        "is_article": bool(article),
        "article_title": article.get("title"),
        "article_preview": article.get("preview_text"),
        "full_text": None,
        "parent": j.get("in_reply_to_status_id_str") or None,
        "quotes": quotes,
        "links": links_of(j),
        "media": photos_of(j),
    }


def candidates_in(html: str, known: set, min_id: str) -> list:
    primary = REST_ID_RE.findall(html) + CONVERSATION_RE.findall(html)
    found = list(dict.fromkeys(primary))
    if found:
        return sorted(
            (c for c in found if id_cmp_key(c) > id_cmp_key(min_id) and c not in known),
            key=id_cmp_key,
        )
    return sorted(
        (
            c
            for c in dict.fromkeys(FALLBACK_ID_RE.findall(html))
            if id_cmp_key(c) > id_cmp_key(min_id) and c not in known
        ),
        key=id_cmp_key,
    )


# --- note full text ----------------------------------------------------------

_norm_re = re.compile(r"\s+")


def norm(s: str) -> str:
    return _norm_re.sub(" ", s).strip()


def json_str(raw: str):
    try:
        v = json.loads(f'"{raw}"')
        return v if isinstance(v, str) else None
    except Exception:
        return None


def decode_json_strings(html: str) -> list:
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")
    out = []
    for m in TEXT_RE.finditer(unescaped):
        v = json_str(m.group(1))
        if v is not None:
            out.append(v)
    return out


def note_key(text: str) -> str:
    normalized = norm(text)
    normalized = re.sub(r"\s*https?://t\.co/\w+\s*$", "", normalized, flags=re.I)
    normalized = re.sub(r"…\s*$", "", normalized).strip()
    return normalized[:120]


def note_full_text(html: str, truncated: str):
    texts = [t for t in decode_json_strings(html) if len(t) > len(truncated) + 20]
    if not texts:
        return None
    key = note_key(truncated)
    if not key:
        return None
    starts = [t for t in texts if norm(t).startswith(key)]
    if not starts:
        return None
    return max(starts, key=len)


def links_from_html(html: str) -> list:
    un = html.replace('\\"', '"').replace("\\\\", "\\")
    out, seen = [], set()
    for m in EXPANDED_RE.finditer(un):
        url = json_str(m.group(1))
        tco = json_str(m.group(2))
        if url is None or tco is None or not tco.startswith("https://t.co/"):
            continue
        if tco in seen:
            continue
        seen.add(tco)
        out.append({"tco": tco, "url": url})
    return out


# --- article full text (addition in this port, not in the original) -----------
# Article posts carry only a title and preview in the syndication payload.
# The page HTML embeds the article as Draft.js style content_state blocks,
# so the body can be rebuilt exactly, block by block, in order.


def _extract_json_array(text: str, start: int):
    """Return the JSON array starting at text[start] == '[', bracket matched
    with string awareness, or None."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except Exception:
                    return None
    return None


def article_code_blocks(page_html: str) -> list:
    """Code blocks in an article render as x-article-code divs in the page
    HTML, outside content_state. Extract their plain text in order."""
    un = page_html.replace('\\"', '"').replace("\\\\", "\\")
    codes = []
    for m in re.finditer(r'class="x-article-code group', un):
        pre_start = un.find("<pre", m.start())
        if pre_start < 0:
            continue
        pre_end = un.find("</pre>", pre_start)
        if pre_end < 0:
            continue
        text = htmlmod.unescape(re.sub(r"<[^>]+>", "", un[pre_start:pre_end])).strip()
        if text and text not in codes:
            codes.append(text)
    return codes


def article_full_text(html: str, title, preview):
    un = html.replace('\\"', '"').replace("\\\\", "\\")
    idx = un.find('"content_state"')
    if idx < 0:
        return None
    bidx = un.find('"blocks":', idx)
    if bidx < 0:
        return None
    start = un.find("[", bidx)
    if start < 0:
        return None
    blocks = _extract_json_array(un, start)
    if not blocks:
        return None
    parts = []  # ("text", str) or ("atomic", None) placeholders, in order
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = b.get("type") or "unstyled"
        text = b.get("text") or ""
        if btype == "atomic":
            parts.append(("atomic", None))
            continue
        if not text.strip():
            continue
        parts.append(("text", f"## {text}" if btype.startswith("header") else text))
    texts = [v for kind, v in parts if kind == "text"]
    if not texts:
        return None
    if preview:
        plain = re.sub(r"^## ", "", "\n\n".join(texts), flags=re.M)
        if not norm(plain).startswith(norm(preview)[:40]):
            return None
    # Splice code blocks in. With exactly one, it belongs at the last
    # atomic block (the usual "paste this prompt" position). With several,
    # append them at the end in order rather than guess positions.
    codes = article_code_blocks(html)
    atomic_at = [i for i, (kind, _) in enumerate(parts) if kind == "atomic"]
    if len(codes) == 1 and atomic_at:
        parts[atomic_at[-1]] = ("text", f"```\n{codes[0]}\n```")
    elif codes:
        for code in codes:
            parts.append(("text", f"```\n{code}\n```"))
    body = "\n\n".join(v for kind, v in parts if kind == "text")
    if title and norm(title) not in norm(body):
        body = f"# {title}\n\n{body}"
    return body


# --- thread assembly ---------------------------------------------------------


class Thread:
    def __init__(self, start_id: str):
        self.start_id = start_id
        self.posts = []
        self.ancestors = []
        self.replies = []
        self.quoted = []
        self.excluded = 0
        self.start_page_ok = True
        self.uncompleted_longform = 0
        self.media_notes = []


def adopt(t: Thread, post: dict, known: set) -> bool:
    if post["author"] == t.posts[0]["author"]:
        if post["parent"] and any(p["id"] == post["parent"] for p in t.posts):
            t.posts.append(post)
            known.add(post["id"])
            return True
    elif post["parent"] and post["parent"] in known:
        t.replies.append(post)
        known.add(post["id"])
        return False
    t.excluded += 1
    known.add(post["id"])
    return False


def probe(t: Thread, ids: list, known: set) -> int:
    added = 0
    for post_id in ids:
        j = SYND.fetch(post_id)
        if not j or "__status" in j:
            t.excluded += 1
            known.add(post_id)
            continue
        if adopt(t, to_tweet(j), known):
            added += 1
    return added


def build(start_id: str) -> Thread:
    t0 = time.time()
    t = Thread(start_id)
    known = set()
    pages = {}

    def load_page(post_id: str, author: str) -> str:
        key = f"{author}/{post_id}"
        if key in pages:
            return pages[key]
        html = fetch_page(post_id, author)
        if html:
            pages[key] = html
        return html

    # Feature 1, up: walk to the root.
    cur = start_id
    while cur and cur not in known and len(t.posts) < 500:
        known.add(cur)
        j = SYND.fetch(cur)
        if not j or "__status" in j:
            t.excluded += 1
            break
        t.posts.append(to_tweet(j))
        cur = t.posts[-1]["parent"]
    if not t.posts:
        raise LookupError("start post unavailable")
    t.posts.sort(key=lambda p: id_cmp_key(p["id"]))
    dlog(f"up-walk: start={start_id} posts={len(t.posts)} root={t.posts[0]['id']}")

    # Split other authors on the start path out of the body.
    root_author = t.posts[0]["author"]
    t.ancestors = [p for p in t.posts if p["author"] != root_author]
    if t.ancestors:
        t.posts = [p for p in t.posts if p["author"] == root_author]

    # The start page is fetched once here and reused below (down-walk seed,
    # reply enumeration and note completion all read from the same cache).
    start_post = next((p for p in t.posts if p["id"] == start_id), None) or next(
        (p for p in t.ancestors if p["id"] == start_id), None
    )
    start_html = ""
    if start_post:
        start_html = load_page(start_id, start_post["author"])
    t.start_page_ok = bool(start_html)

    # Feature 1, down: extend the author's self-chain to its end.
    for hop in range(MAX_HOPS):
        if not t.posts:
            break
        newest = max(t.posts, key=lambda p: id_cmp_key(p["id"]))["id"]
        cands = []
        for attempt in range(PAGE_ATTEMPTS):
            html = load_page(newest, t.posts[0]["author"])
            cands = candidates_in(html, known, newest)
            if cands:
                break
            dlog(f"thin page for {newest} len={len(html)} attempt={attempt}")
            time.sleep(1.5 * (attempt + 1))
        if not cands:
            break
        added = probe(t, cands, known)
        dlog(f"hop{hop}: cands={len(cands)} added={added}")
        if not added:
            break
    dlog(f"down-walk: posts={len(t.posts)} replies={len(t.replies)} excluded={t.excluded}")

    # Feature 2: replies visible on the start page.
    if start_html:
        extra = candidates_in(start_html, known, "0")
        if extra:
            probe(t, extra, known)

    # Feature 4: complete note tweets from their pages. Article posts take
    # the parallel path added in this port (title and preview only arrive
    # via syndication, the body lives in the page's content_state blocks).
    for p in t.posts:
        if p["is_article"]:
            page_html = load_page(p["id"], p["author"])
            full = article_full_text(page_html, p["article_title"], p["article_preview"])
            if full:
                p["full_text"] = full
                dlog(f"article completed: {p['id']} -> {len(full)} chars")
            else:
                t.uncompleted_longform += 1
            continue
        if not p["is_note"]:
            continue
        page_html = load_page(p["id"], p["author"])
        full = note_full_text(page_html, p["text"])
        if full and len(full) > len(p["text"]):
            p["full_text"] = full
            seen = {l["tco"] for l in p["links"]}
            for link in links_from_html(page_html):
                if link["tco"] not in seen:
                    p["links"].append(link)
            dlog(f"note completed: {p['id']} {len(p['text'])} -> {len(full)}")
        else:
            t.uncompleted_longform += 1

    t.posts.sort(key=lambda p: id_cmp_key(p["id"]))
    t.replies.sort(key=lambda p: id_cmp_key(p["id"]))

    # Feature 3: follow quotes until no new quote appears.
    post_ids = {p["id"] for p in t.posts}
    info = {}
    for p in t.posts:
        for q in p["quotes"]:
            info[q["id"]] = {"q": q, "quote_of": p["id"]}
    order = list(info.keys())
    done = set()
    i = 0
    while i < len(order):
        qid = order[i]
        i += 1
        if qid in done:
            continue
        done.add(qid)
        j = SYND.fetch(qid)
        if j and "__status" not in j:
            entry = info.get(qid)
            if entry:
                entry["q"]["author"] = (j.get("user") or {}).get("screen_name") or entry["q"]["author"]
                entry["q"]["text"] = j.get("text") or entry["q"]["text"]
                entry["q"]["links"] = links_of(j)
                entry["q"]["media"] = photos_of(j)
            qt = j.get("quoted_tweet") or {}
            if qt.get("id_str"):
                nid = str(qt["id_str"])
                if nid not in info:
                    info[nid] = {
                        "q": {
                            "id": nid,
                            "author": (qt.get("user") or {}).get("screen_name") or "?",
                            "text": qt.get("text") or "",
                            "links": links_of(qt),
                            "media": photos_of(qt),
                        },
                        "quote_of": qid,
                    }
                    order.append(nid)
    dlog(f"quoted walk: {len(info)} unique, {len(done)} refetched")
    for qid in order:
        if qid in post_ids or any(c["id"] == qid for c in t.quoted):
            continue
        entry = info.get(qid)
        if entry:
            t.quoted.append({**entry["q"], "quote_of": entry["quote_of"]})

    dlog(f"build total: ms={int((time.time() - t0) * 1000)}")
    return t


# --- media download (opt-in, --media) ----------------------------------------
# Photos only: videos and animated GIFs never reach this code (photos_of
# drops them). Downloads are the small variant; each JSON record also
# carries the medium URL so the reader can escalate on its own. Files land
# under MEDIA_DIR/<post id>/ and an existing file is reused, not refetched.


def _media_path(post_id: str, index: int, url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    return os.path.join(MEDIA_DIR, post_id, f"{index}-{MEDIA_VARIANT}{ext}")


def _media_line(f: dict) -> str:
    dims = f"{f['width']}x{f['height']}" if f.get("width") and f.get("height") else ""
    return f"[media] photo {dims} -> {f['path']}".replace("  ", " ")


def fetch_media(posts) -> list:
    """Download photos for an iterable of post/quoted dicts, setting
    p["media_files"] on each that has any. Returns failure notes.
    A transport failure in direct mode raises DirectError (exit 4);
    HTTP errors and oversize bodies degrade to notes in both modes."""
    notes = []
    failed = 0
    count = 0
    capped = False
    for p in posts:
        files = []
        for i, m in enumerate(p.get("media") or []):
            if count >= MEDIA_MAX_FILES:
                capped = True
                break
            path = _media_path(p["id"], i + 1, m["url"])
            if not (os.path.isfile(path) and os.path.getsize(path) > 0):
                req = urlparse(m["url"]).path + f"?name={MEDIA_VARIANT}"
                try:
                    status, _h, body = MEDIA_CLIENT.get(
                        req,
                        headers={"user-agent": USER_AGENT, "accept": "image/*,*/*"},
                    )
                except Exception as e:
                    if DIRECT_NETWORK:
                        raise _directnet().DirectError(
                            f"direct: media fetch failed for post {p['id']}: {e}"
                        )
                    failed += 1
                    continue
                if status != 200 or not body or len(body) > MEDIA_MAX_BYTES:
                    failed += 1
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(body)
            count += 1
            files.append(
                {
                    "type": "photo",
                    "width": m.get("width"),
                    "height": m.get("height"),
                    "path": path,
                    "url": m["url"],
                    "medium_url": m["url"] + "?name=medium",
                }
            )
        if files:
            p["media_files"] = files
    if capped:
        notes.append(
            f"media download stopped after {MEDIA_MAX_FILES} files (run cap); "
            "later images were not fetched."
        )
    if failed:
        notes.append(f"{failed} image(s) could not be downloaded.")
    return notes


# --- rendering ---------------------------------------------------------------


def post_text(p: dict) -> str:
    return expand_links(p.get("full_text") or p["text"], p["links"])


def render(t: Thread) -> str:
    head = (
        f"[x-thread] @{t.posts[0]['author']} / {len(t.posts)} posts / "
        f"root={t.posts[0]['id']} / start={t.start_id}"
    )
    lines = [head]
    for i, p in enumerate(t.posts):
        lines += ["", f"{i + 1}. {p['id']} · {p.get('created_at') or '?'}", post_text(p)]
        for f in p.get("media_files") or []:
            lines.append(_media_line(f))
    if t.quoted:
        lines += ["", f"[x-thread] quoted by the thread ({len(t.quoted)}):"]
        for q in t.quoted:
            lines.append(f"- {q['id']} · @{q['author']} · quoted by {q['quote_of']}")
            lines.append(f"  {expand_links(q['text'], q['links']).replace(chr(10), ' ')}")
            for f in q.get("media_files") or []:
                lines.append(f"  {_media_line(f)}")
    if t.ancestors:
        lines += [
            "",
            f"[x-thread] start path between the start post and the root, "
            f"by other authors ({len(t.ancestors)}):",
        ]
        for p in t.ancestors:
            lines.append(f"- {p['id']} · @{p['author']} · {p.get('created_at') or '?'} → {p['parent'] or '-'}")
            lines.append(f"  {post_text(p).replace(chr(10), ' ')}")
            for f in p.get("media_files") or []:
                lines.append(f"  {_media_line(f)}")
    if t.replies:
        lines += ["", f"[x-thread] replies by others ({len(t.replies)}):"]
        for p in t.replies:
            lines.append(f"- {p['id']} · @{p['author']} · {p.get('created_at') or '?'} → {p['parent']}")
            lines.append(f"  {post_text(p).replace(chr(10), ' ')}")
    notes = []
    if not t.start_page_ok:
        notes.append(
            "the start post's page could not be fetched, so the continuation, "
            "the replies and long-form completion may be missing. The posts "
            "above come from syndication and are complete as far as they go."
        )
    if t.uncompleted_longform:
        notes.append(
            f"{t.uncompleted_longform} long-form post(s) could not be completed "
            "from their pages, so their text may be truncated."
        )
    if t.replies:
        notes.append(
            "the reply list shows only what the public page exposes without a "
            "login, so its count is a floor, not a total."
        )
    notes += t.media_notes
    if notes:
        lines += ["", "[x-thread] notes:"]
        lines += [f"- {n}" for n in notes]
    return "\n".join(lines)


def to_json(t: Thread) -> dict:
    def shape(p, full=False):
        d = {
            "id": p["id"],
            "author": p["author"],
            "created_at": p.get("created_at"),
            "text": post_text(p),
            "is_note": p["is_note"],
            "is_article": p.get("is_article", False),
            "article_title": p.get("article_title"),
            "note_completed": bool(p.get("full_text")),
            "parent": p["parent"],
            "url": f"https://x.com/{p['author']}/status/{p['id']}",
        }
        if p.get("media_files"):
            d["media"] = p["media_files"]
        return d

    def _shape_quoted(q):
        d = {
            "id": q["id"],
            "author": q["author"],
            "text": expand_links(q["text"], q["links"]),
            "quote_of": q["quote_of"],
            "url": f"https://x.com/{q['author']}/status/{q['id']}",
        }
        if q.get("media_files"):
            d["media"] = q["media_files"]
        return d

    chain_ok = all(
        i == 0 or any(q["id"] == p["parent"] for q in t.posts)
        for i, p in enumerate(t.posts)
    )
    return {
        "start_id": t.start_id,
        "root_id": t.posts[0]["id"] if t.posts else None,
        "author": t.posts[0]["author"] if t.posts else None,
        "posts": [shape(p) for p in t.posts],
        "ancestors": [shape(p) for p in t.ancestors],
        "replies": [shape(p) for p in t.replies],
        "quoted": [_shape_quoted(q) for q in t.quoted],
        "excluded": t.excluded,
        "diagnostics": {
            "chain_ok": chain_ok,
            "start_included": any(
                p["id"] == t.start_id for p in t.posts + t.ancestors + t.replies
            ),
            "notes_completed": sum(1 for p in t.posts if p.get("full_text")),
            "tco_remaining": len(re.findall(r"https://t\.co/\w+", render(t))),
            "start_page_ok": t.start_page_ok,
            "uncompleted_longform": t.uncompleted_longform,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch an X post/thread without a browser.")
    ap.add_argument("target", help="status URL (x/twitter/fxtwitter/vxtwitter/oembed) or bare post ID")
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of the text digest")
    ap.add_argument(
        "--mode",
        choices=["thread", "post"],
        default="thread",
        help="thread builds the whole author thread (default). post reads only the one post via syndication, with no page fetch.",
    )
    ap.add_argument(
        "--direct-network",
        action="store_true",
        help="connect directly to the origin, ignoring any forward proxy in the environment. Names are resolved from a local zone file or DNS-over-HTTPS. Fail-closed: a direct failure exits with code 4 and never falls back to the proxy.",
    )
    ap.add_argument(
        "--media",
        action="store_true",
        help="also download photo attachments (small variant) to ~/workspace/x-thread-media/<post id>/ and list the saved paths in the output. Videos and GIFs are not supported.",
    )
    args = ap.parse_args(argv)

    global DIRECT_NETWORK, MEDIA_ENABLED
    if args.direct_network:
        DIRECT_NETWORK = True
    if args.media:
        MEDIA_ENABLED = True

    target = status_target(args.target)
    if target is None:
        print(f"error: not an X status URL or post ID: {args.target}", file=sys.stderr)
        return 2
    _author_hint, start_id = target

    if args.mode == "post":
        try:
            j = SYND.fetch(start_id)
        except Exception as e:
            if DIRECT_NETWORK:
                print(f"error: direct-network failed: {e}", file=sys.stderr)
                return 4
            raise
        if not j or "__status" in j:
            print(f"error: post {start_id} unavailable via syndication", file=sys.stderr)
            return 3
        p = to_tweet(j)
        media_notes = []
        if MEDIA_ENABLED:
            try:
                media_notes = fetch_media([p])
            except Exception as e:
                if DIRECT_NETWORK:
                    print(f"error: direct-network failed: {e}", file=sys.stderr)
                    return 4
                raise
        if args.json:
            pd = {
                "id": p["id"], "author": p["author"], "created_at": p["created_at"],
                "text": post_text(p), "is_note": p["is_note"],
                "is_article": p["is_article"], "article_title": p["article_title"],
                "parent": p["parent"],
                "url": f"https://x.com/{p['author']}/status/{p['id']}",
            }
            if p.get("media_files"):
                pd["media"] = p["media_files"]
            print(json.dumps({"posts": [pd]}, ensure_ascii=False, indent=2))
        else:
            print(f"[x-thread] @{p['author']} / 1 post / start={start_id}")
            print(f"\n1. {p['id']} · {p.get('created_at') or '?'}")
            print(post_text(p))
            for f in p.get("media_files") or []:
                print(_media_line(f))
            if p["is_note"] or p["is_article"]:
                print("\n[x-thread] long-form post: text may be truncated in post mode. Use --mode thread for the full text.")
            if media_notes:
                print("\n[x-thread] notes:")
                for n in media_notes:
                    print(f"- {n}")
        return 0

    try:
        t = build(start_id)
    except LookupError:
        print(f"error: start post {start_id} unavailable via syndication", file=sys.stderr)
        return 3
    except Exception as e:
        if DIRECT_NETWORK:
            print(f"error: direct-network failed: {e}", file=sys.stderr)
            return 4
        raise

    if MEDIA_ENABLED:
        try:
            t.media_notes = fetch_media(
                list(t.posts) + list(t.ancestors) + list(t.quoted)
            )
        except Exception as e:
            if DIRECT_NETWORK:
                print(f"error: direct-network failed: {e}", file=sys.stderr)
                return 4
            raise

    if args.json:
        print(json.dumps(to_json(t), ensure_ascii=False, indent=2))
    else:
        digest = render(t)
        print(digest)
        if os.environ.get("X_THREAD_DEBUG") == "1":
            path = os.environ.get("X_THREAD_RENDER", "/tmp/x-thread-render.txt")
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n########## start={start_id} ##########\n{digest}\n")
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
