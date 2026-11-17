"""
NEXUS — NY511 Scraper
=====================
Fetches traffic cameras from the NY511 system (511ny.org and NYSDOT sources).
API: references 511ny.org page sources and NYSDOT skyvdn m3u8 streams.
No API key required.
"""

import json
import time
import os
import re
import requests
from urllib.parse import urlparse, parse_qs

from scrapers.utils import log, log_progress, build_feature, HEADERS

PLUGIN_META = {
    "name":         "NY511 NY State",
    "key_required": False,
    "description":  "New York State traffic cameras via 511ny.org and NYSDOT",
}


_API_BASE = "https://511ny.org"


def fetch_stations(config=None):
    """Fetch all camera stations from 511ny.org."""
    try:
        resp = requests.get(f"{_API_BASE}/api/cameras", headers=HEADERS, timeout=config.get("TIMEOUT", 15))
        resp.raise_for_status()
        data = resp.json()
        return data.get("stations", [])
    except Exception as e:
        log(f"Error fetching NY511 stations: {e}", "WARN")
        return []


def fetch_station_details(station_id, config=None):
    """Fetch details for a single station."""
    try:
        resp = requests.get(f"{_API_BASE}/api/station/{station_id}", headers=HEADERS, timeout=config.get("TIMEOUT", 15))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Error fetching station {station_id}: {e}", "WARN")
        return None


def fetch(stream_config=None, config=None):
    """Main entry point for the scraper pipeline.
    
    Returns (features, error).
    """
    features = []
    stations = fetch_stations(stream_config or {})
    
    log(f"Fetching {len(stations)} stations from NY511", "INFO")
    
    for i, station in enumerate(stations):
        try:
            details = fetch_station_details(station.get("id"), stream_config or {})
            if not details:
                continue
            
            # Extract feed info
            feed_url = details.get("feed_url") or ""
            stream_url = details.get("stream_url") or ""
            feed_type = details.get("feed_type", "image").lower()
            city = details.get("city") or ""
            state = details.get("state") or ""
            country = "US"
            cam_type = "traffic"
            source = "ny511"
            
            # Build feature
            try:
                lat = float(details.get("lat", 0) or 0)
                lon = float(details.get("lon", 0) or 0)
            except (ValueError, TypeError):
                lat = 40.7128  # fallback NYC
                lon = -74.0060
            
            name = details.get("name") or f"NY511 Station {station.get('id', i)}"
            
            feat = build_feature(
                cam_id=str(details.get("id", "")),
                name=name,
                lat=lat,
                lon=lon,
                feed_url=feed_url,
                cam_type=cam_type,
                city=city,
                country=country,
                source=source,
                stream_url=stream_url,
            )
            features.append(feat)
            
            # Rate limit
            time.sleep(config.get("REQUEST_DELAY", 0.5))
            
        except Exception as e:
            log(f"Error processing station {station.get('id')}: {e}", "WARN")
            continue
    
    return features, ""


def _list_plugins():
    pass


def _run_plugin(alias, config):
    pass


def _resolve_plugins(args):
    pass