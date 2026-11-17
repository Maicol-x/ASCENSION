#!/usr/bin/env python3
"""Shared "does this camera actually work?" probe.

A camera that is listed but always opens an offline player is worse than no
camera at all, so the pipeline needs a cheap, honest test of the feed itself.
This module is the single implementation used by the one-off sampler
(`probe_health.py`) and by the continuous checker (`verify_feeds.py`):
same timeouts, same validators, so the numbers they produce are comparable.

The probe checks the URL the frontend would load:
  * `streamUrl` when present (HLS/mp4/mjpeg players),
  * otherwise `feedUrl` (snapshot, iframe page or TxDOT JSON).
It first tries the URL directly and then, when the failure was an HTTP-level
one, through the local proxy — exactly the fallback order of the app.
"""
import json
import threading
import time
import urllib.parse
from collections import defaultdict

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")

# Neutral reachability targets. They are deliberately *not* camera providers:
# provider hosts can 403/404 under our own probe load or rotate URLs, which
# would make a healthy network look broken. A plain TCP handshake to unrelated
# infrastructure is the honest test for "is this machine online?".
CONTROL_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("www.github.com", 443))


def looks_image(body: bytes) -> bool:
    if any(body.startswith(m) for m in IMAGE_MAGIC):
        return True
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return True
    return False


def validate(feed_type: str, resp, body: bytes):
    """Decide whether a response is a working feed. Returns (ok, reason)."""
    ct = (resp.headers.get("content-type") or "").lower()
    url = (resp.url or "").lower().split("?")[0]

    if feed_type == "iframe":
        xfo = (resp.headers.get("x-frame-options") or "").lower()
        csp = (resp.headers.get("content-security-policy") or "").lower()
        if "deny" in xfo or "sameorigin" in xfo:
            return False, "iframe_blocked_xfo"
        if "frame-ancestors" in csp and "*" not in csp.split("frame-ancestors", 1)[1][:80]:
            return False, "iframe_blocked_csp"
        if resp.status_code == 200 and ("text/html" in ct or not ct):
            return True, "iframe_ok"
        return False, "iframe_bad"

    if "text/html" in ct:
        return False, "html_error_page"

    if feed_type == "txdot-json" or url.endswith(".json") or "json" in ct:
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return False, "json_parse"
        if isinstance(obj, dict):
            snippet = obj.get("snippet")
            if isinstance(snippet, str) and len(snippet) > 100:
                return True, "txdot_ok"
            if any(k in obj for k in ("image", "imageUrl", "url", "data")):
                return True, "json_ok"
        return False, "json_no_image"

    if feed_type in ("m3u8", "mp4") or "mpegurl" in ct or url.endswith(".m3u8"):
        if b"#EXTM3U" in body:
            return True, "hls_ok"
        if "video/" in ct and len(body) > 1024:
            return True, "video_ok"
        return False, "hls_bad"

    if looks_image(body):
        return True, "image_ok" if len(body) >= 200 else "image_too_small"
    if "image/" in ct and len(body) > 200:
        return True, "image_ct_ok"
    if ct.startswith("video/"):
        return True, "video_ct_ok"
    return False, "bad_content"


def probe(url: str, feed_type: str, proxy_base: str | None,
          timeout: float = 5.0, proxy_timeout: float = 14.0,
          host_sem: dict | None = None, proxy_sem=None) -> dict:
    """Probe one feed. Returns {ok, via, reason, ms, status}."""
    started = time.time()
    session = requests.Session()
    session.trust_env = False
    out = {"ok": False, "via": "", "reason": "", "ms": 0, "status": 0, "hard": False}
    should_proxy = True

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    sem = host_sem[host] if host_sem is not None else _NullSem()

    try:
        with sem:
            r = session.get(url, timeout=(min(6, timeout), timeout), stream=True,
                            allow_redirects=True, headers={"User-Agent": UA})
        body = r.raw.read(8192, decode_content=True) or b""
        out["status"] = r.status_code
        r.close()
        if r.status_code == 200:
            ok, reason = validate(feed_type, r, body)
            out["reason"] = reason
            if ok:
                out.update(ok=True, via="direct", ms=int((time.time() - started) * 1000))
                return out
        elif r.status_code in (404, 410):
            out.update(reason=f"http_{r.status_code}", ms=int((time.time() - started) * 1000))
            return out
        else:
            out["reason"] = f"http_{r.status_code}"
    except requests.exceptions.Timeout:
        out["reason"] = "Timeout"
    except requests.exceptions.SSLError:
        out["reason"] = "SSLError"
        # The proxy shares our resolver and trust store, so retrying adds nothing.
        should_proxy = False
    except requests.exceptions.ConnectionError:
        out["reason"] = "ConnectionError"
        should_proxy = False
    except Exception as exc:
        out["reason"] = type(exc).__name__

    if should_proxy and proxy_base:
        try:
            purl = proxy_base + urllib.parse.quote(url, safe="")
            cm = proxy_sem if proxy_sem is not None else _NullSem()
            with cm:
                r = session.get(purl, timeout=(min(6, proxy_timeout), proxy_timeout),
                                stream=True, allow_redirects=True,
                                headers={"User-Agent": UA})
            body = r.raw.read(8192, decode_content=True) or b""
            status = r.status_code
            r.close()
            if status == 200:
                ok, reason = validate(feed_type, r, body)
                if ok:
                    out.update(ok=True, via="proxy", reason=reason,
                               ms=int((time.time() - started) * 1000))
                    return out
                out["reason"] = reason
            else:
                out["reason"] = f"proxy_http_{status}"
        except Exception as exc:
            out["reason"] = f"proxy_{type(exc).__name__}"

    out["ms"] = int((time.time() - started) * 1000)
    return out


class _NullSem:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def make_limiters(host_limit: int = 6, proxy_limit: int = 36):
    """Per-host + global-proxy limiters shared by a whole probe run."""
    return defaultdict(lambda: threading.BoundedSemaphore(host_limit)), threading.BoundedSemaphore(proxy_limit)


def network_ok(minimum: int = 2, timeout: float = 4.0) -> bool:
    """Gate every destructive verdict behind a reachability check.

    If this machine cannot even open a TCP connection to unrelated
    infrastructure, the fault is the local connection, not thousands of dead
    cameras, and the run must not deactivate anything.
    """
    import socket
    ok = 0
    for host, port in CONTROL_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok += 1
        except OSError:
            continue
    return ok >= minimum
