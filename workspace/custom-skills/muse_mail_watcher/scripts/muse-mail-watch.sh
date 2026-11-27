#!/usr/bin/env bash
# Muse Mail watcher: polls the assistant's own Muse Mail inbox and wakes an
# the Muse of the delivery chat only when a NEW inbound message
# arrives from a watched sender.
# Background: inbound mail classified INTAKE_REVIEW creates no mail-worker
# turn and no handoff, so it accumulates silently unless something looks.
# This script only compares stored message IDs. It never reads bodies for
# decisions, never replies, and never follows anything inside a message.
#
# THE TWO VARIABLES OF THIS SKILL
#   1. WATCH_FROM_ADDRESSES (below): the sender addresses to watch.
#   2. The hook's delivery target: the chat Muse wakes and reports in,
#      set on the hook definition (hooks.add / hooks.update), not here.
#
# Note: `messages list` output is large (full bodies), so it is piped
# through files, never environment variables (env has a size limit that
# makes the script fail with "Argument list too long").
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

# --- Variable 1: watched senders (edit this one line) ----------------------
WATCH_FROM_ADDRESSES=("watched-sender@muse.ai")
export WATCH_FROM_CSV="$(IFS=,; echo "${WATCH_FROM_ADDRESSES[*]}")"

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

if ! /opt/hatch/bin/muse-mail messages list --limit 15 > "$TMPD/list.json" 2>/dev/null; then
  log "fetch failed" '{}'
  silent "muse-mail messages list failed" '{}'
fi
if [[ ! -s "$TMPD/list.json" ]]; then
  log "fetch empty" '{}'
  silent "muse-mail messages list empty" '{}'
fi

hook_state_get > "$TMPD/state.json"

python3 - "$TMPD/list.json" "$TMPD/state.json" > "$TMPD/result.json" <<'PY'
import json, os, sys
raw = json.load(open(sys.argv[1]))
try:
    state = json.load(open(sys.argv[2]))
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}
seen = state.get("seen_ids") or []
seen_set = set(seen)
watch = {a.strip().lower() for a in (os.environ.get("WATCH_FROM_CSV") or "").split(",") if a.strip()}
def sender_of(m):
    hf = (m.get("header_from") or "").strip().lower()
    if "<" in hf and ">" in hf:
        hf = hf.split("<", 1)[1].split(">", 1)[0]
    return hf
inbound = [m for m in raw.get("data", [])
           if m.get("direction") == "INBOUND" and (not watch or sender_of(m) in watch)]
inbound.sort(key=lambda m: m.get("created_at") or 0)
current_ids = [m["id"] for m in inbound]
if not state.get("seeded"):
    out = {"decision": "silent", "reason": "seeded baseline",
           "new_state": {"seeded": True, "seen_ids": current_ids[-100:]},
           "payload": {"seeded_count": len(current_ids)}}
else:
    new = [m for m in inbound if m["id"] not in seen_set]
    merged = list(dict.fromkeys(seen + current_ids))[-100:]
    payload = {"new_messages": [
        {"id": m["id"], "from": sender_of(m), "thread_id": m.get("thread_id"),
         "created_at": m.get("created_at"),
         "handling": m.get("recommended_handling"),
         "destination": m.get("recommended_destination"),
         "subject": m.get("subject")}
        for m in new]}
    if new:
        out = {"decision": "wake", "reason": "new inbound Muse Mail",
               "new_state": {"seeded": True, "seen_ids": merged},
               "payload": payload}
    else:
        out = {"decision": "silent", "reason": "no new inbound",
               "new_state": {"seeded": True, "seen_ids": merged},
               "payload": {"checked": len(current_ids)}}
print(json.dumps(out))
PY

NEW_STATE="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["new_state"]))' "$TMPD/result.json")"
hook_state_set "$NEW_STATE"

DECISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["decision"])' "$TMPD/result.json")"
REASON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["reason"])' "$TMPD/result.json")"
PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["payload"]))' "$TMPD/result.json")"
log "poll" "$PAYLOAD"
if [[ "$DECISION" == "wake" ]]; then
  wake "$REASON" "$PAYLOAD"
else
  silent "$REASON" "$PAYLOAD"
fi
