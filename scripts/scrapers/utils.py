"""
Utility functions for the scraper pipeline.
Provides logging, progress reporting, and helper functions.
"""

import sys
import time
import os
import re
from datetime import datetime

# HTTP headers used for requests to government/corporate websites.
# Mimics a real browser to avoid basic bot blocks.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def log(msg: str, status: str = "INFO", *args, **kwargs):
    """Print a status message with status prefix.

    Uses ASCII icons to avoid Unicode encoding issues on Windows cp1252 consoles.
    """
    icons = {"INFO": "[INFO]", "OK": "[OK]", "WARN": "[WARN]", "ERROR": "[ERROR]"}
    icon = icons.get(status, "[INFO]")
    try:
        print(f"  {icon} [{status.upper()}] {msg}", *args, **kwargs)
    except Exception:
        # Fallback: plain print without formatting
        print(f"[{status.upper()}] {msg}", *args, **kwargs)


def log_progress(plugin: str, done: int, total: int, start_time: float, approx: bool = False):
    """Print a progress line with a simple ASCII bar + ETA."""
    done = min(done, total)
    percent = (done / total * 100) if total else 0
    bar_length = 20
    filled_length = int(bar_length * done // total) if total else 0
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    elapsed = time.time() - start_time
    eta = elapsed / done * (total - done) if done > 0 and done < total else 0
    eta_str = f"ETA {elapsed / done * (total - done):.0f}s" if done > 0 and done < total else "—"
    print(f"  [{plugin}] |{bar}| {percent:3.0f}% ({done}/{total}) {eta_str}")


def format_duration(seconds: float | None) -> str:
    """Human ETA: '45s', '3m20s', '1h04m', or '—' when unknown."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def build_feature(cam_id: str, name: str, lat: float, lon: float,
                  feed_url: str, cam_type: str, city: str, country: str,
                  source: str, player_url: str = "", stream_url: str = "",
                  feed_type: str = "image/jpeg", direct_eligible: bool = None,
                  update_rate: int = None, **kwargs) -> dict:
    """Consistently build a GeoJSON Feature for a camera.

    direct_eligible: set True/False to declare whether the feed embeds directly
        (opencctv passes force_direct). Leave None for native sources — the store
        infers it from the historical allowlist so behavior is unchanged.
    update_rate: minimum refresh interval in ms, if the source publishes one.
    """
    props = {
        "id":        f"{source}_{cam_id}",
        "name":      name,
        "type":      cam_type,
        "city":      city,
        "country":   country,
        "feedUrl":   feed_url,
        "playerUrl": player_url,
        "streamUrl": stream_url,
        "feedType":  feed_type,
        "source":    source,
    }
    if direct_eligible is not None:
        props["directEligible"] = bool(direct_eligible)
    if update_rate is not None:
        props["updateRate"] = update_rate
    props.update(kwargs)
    return {
        "type":     "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


# ─────────────────────────────────────────────────────────
# GeoJSON I/O
# ─────────────────────────────────────────────────────────

def load_geojson(path: str) -> dict:
    """Load an existing GeoJSON file, or return an empty FeatureCollection."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"type": "FeatureCollection", "features": []}
    return {"type": "FeatureCollection", "features": []}


def save_geojson(path: str,geojson: dict):
    """Save a GeoJSON file atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────
# Plugin registry helpers
# ─────────────────────────────────────────────────────────

def _list_plugins():
    pass


def _run_plugin(alias: str, config: dict) -> tuple:
    """Import and execute a plugin's fetch(config). Returns (alias, features, error)."""
    from importlib import import_module
    from scrapers.utils import log
    info = PLUGIN_REGISTRY.get(alias)
    if not info:
        return alias, [], f"Unknown plugin alias '{alias}'"
    module = import_module(info["module"])
    try:
        features, error = module.fetch(config)
        log(f"Plugin '{alias}': {len(features) if features else 0} features fetched", "OK")
        return alias, features, error
    except Exception as e:
        log(f"Plugin '{alias}' crashed: {e}", "ERROR")
        return alias, [], str(e)


def _resolve_plugins(args) -> list:
    """Return the ordered list of plugin aliases to run."""
    selected = list(PLUGIN_REGISTRY.keys()) if args.all else (args.plugins or [])
    return selected