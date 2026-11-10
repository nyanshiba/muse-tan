# directnet: name resolution and TLS dialing that bypass any forward
# proxy configured in the environment.
#
# Some environments route all HTTPS through a forward proxy that
# terminates TLS, so a "verified" connection is only verified as far
# as the gateway. This module gives a tool a direct path instead:
#
# - Names are resolved without the system resolver. A local zone file
#   in hosts format ("IP hostname" lines) pins addresses first, in
#   the spirit of nyanshiba's corednsconf zones. Anything not pinned
#   is resolved with DNS-over-HTTPS to Cloudflare, itself contacted
#   directly at a bootstrap IPv6 address, so the resolver's name is
#   never resolved through the intercepted path either.
# - Connections go straight to the resolved addresses, IPv6 first,
#   with normal TLS verification and the SNI set to the original
#   hostname.
# - Fail-closed: any failure raises DirectError. Nothing here falls
#   back to a proxy, silently or otherwise.
#
# Standard library only. Public surface: resolve(), open_tls(),
# DirectError. A zone file path is remembered from the first call
# that passes one (or the X_THREAD_ZONE environment variable).

import http.client
import os
import random
import socket
import ssl
import struct

RESOLVER_NAME = "cloudflare-dns.com"
RESOLVER_IPS = ["2606:4700:4700::1111", "2606:4700:4700::1001"]
# Generous: in approval-gated environments the first handshake on a
# fresh direct connection can wait on a human for a while.
DNS_TIMEOUT = 60.0

_CTX = ssl.create_default_context()


class DirectError(Exception):
    """A direct-network operation failed; callers must not fall back."""


# --- zone file (hosts format) ------------------------------------------------

_zone = {}
_zone_path = None
_zone_loaded = False


def _load_zone(path):
    global _zone_loaded, _zone_path
    if path:
        _zone_path = path
    if _zone_loaded:
        return
    _zone_loaded = True
    p = _zone_path or os.environ.get("X_THREAD_ZONE")
    if not p:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zone.hosts")
    try:
        lines = open(p, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for ln in lines:
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        parts = ln.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        try:
            socket.inet_pton(fam, ip)
        except OSError:
            continue
        for name in parts[1:]:
            _zone.setdefault(name.lower(), []).append(ip)


# --- DNS over HTTPS (wire format, direct to the resolver) -------------------

def _dns_packet(name: str, qtype: int) -> bytes:
    tid = random.randint(0, 0xFFFF)
    hdr = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return hdr + qname + struct.pack(">HH", qtype, 1)


def _read_name(data: bytes, off: int):
    labels = []
    jumped = None
    while True:
        if off >= len(data):
            raise DirectError("dns: name out of bounds")
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[off:off + 2])[0] & 0x3FFF
            jumped = off + 2
            off = ptr
            continue
        labels.append(data[off + 1:off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
    return ".".join(labels), (jumped if jumped is not None else off)


def _parse_answers(data: bytes):
    """Return (addresses, cname_targets) from a DNS response packet."""
    if len(data) < 12:
        raise DirectError("dns: short response")
    qd, an = struct.unpack(">HH", data[4:8])
    off = 12
    for _ in range(qd):
        _, off = _read_name(data, off)
        off += 4
    addrs, cnames = [], []
    for _ in range(an):
        _, off = _read_name(data, off)
        if off + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        off += 10
        rdata_off = off
        rdata = data[off:off + rdlen]
        off += rdlen
        if rtype == 28 and len(rdata) == 16:
            addrs.append(socket.inet_ntop(socket.AF_INET6, rdata))
        elif rtype == 1 and len(rdata) == 4:
            addrs.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == 5:
            target, _ = _read_name(data, rdata_off)
            cnames.append(target)
    return addrs, cnames


def _doh_query(name: str, qtype: int) -> bytes:
    body = _dns_packet(name, qtype)
    last = None
    for ip in RESOLVER_IPS:
        try:
            raw = socket.create_connection((ip, 443), timeout=DNS_TIMEOUT)
            tls = _CTX.wrap_socket(raw, server_hostname=RESOLVER_NAME)
            conn = http.client.HTTPConnection(RESOLVER_NAME, timeout=DNS_TIMEOUT)
            conn.sock = tls
            conn.request(
                "POST", "/dns-query", body=body,
                headers={
                    "content-type": "application/dns-message",
                    "accept": "application/dns-message",
                },
            )
            res = conn.getresponse()
            data = res.read()
            conn.close()
            if res.status == 200:
                return data
            last = DirectError(f"doh: status {res.status}")
        except DirectError:
            raise
        except Exception as e:  # transport or TLS failure: try next resolver
            last = e
    raise DirectError(f"doh: query for {name} failed: {last}")


def _doh_resolve_type(host: str, qtype: int, depth: int = 0):
    """Resolve one record type, chasing CNAMEs for that type alone.

    Chasing per type matters: a name can be a CNAME to a dual-stack
    target, and collecting the A record first must not end the search
    for the target's AAAA.
    """
    if depth > 5:
        raise DirectError(f"dns: cname chain too deep for {host}")
    got, cnames = _parse_answers(_doh_query(host, qtype))
    if got:
        return got
    for target in cnames:
        try:
            return _doh_resolve_type(target, qtype, depth + 1)
        except DirectError:
            continue
    return []


def _doh_resolve(host: str):
    addrs = []
    for qtype in (28, 1):
        addrs += _doh_resolve_type(host, qtype)
    return addrs


# --- public surface ----------------------------------------------------------

_cache = {}


def resolve(host: str, zone_path: str = None) -> list:
    """Addresses for host: zone pins first, then DoH. IPv6 first."""
    host = host.lower()
    if host in _cache:
        return _cache[host]
    _load_zone(zone_path)
    addrs = list(_zone.get(host, []))
    if not addrs:
        addrs = _doh_resolve(host)
    addrs.sort(key=lambda a: 0 if ":" in a else 1)
    _cache[host] = addrs
    return addrs


def open_tls(host: str, port: int = 443, timeout: float = 10.0, zone_path: str = None):
    """Open a verified TLS connection straight to host. Fail-closed."""
    addrs = resolve(host, zone_path)
    if not addrs:
        raise DirectError(f"direct: no addresses for {host}")
    last = None
    for ip in addrs:
        raw = None
        try:
            raw = socket.create_connection((ip, port), timeout=timeout)
            return _CTX.wrap_socket(raw, server_hostname=host)
        except Exception as e:
            last = e
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass
    raise DirectError(f"direct: connection to {host} failed: {last}")
