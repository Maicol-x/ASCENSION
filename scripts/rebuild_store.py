#!/usr/bin/env python3
"""Rebuild the SQLite store from a published GeoJSON, then re-export payloads.

This is the recovery path for the regression where a partial rebuild blanked
`feed_url`, `stream_url` and `direct_eligible` for every row: the map then had
zero displayable cameras. It is also the safe way to seed a fresh environment.

Usage:
    python scripts/rebuild_store.py [source.geojson]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402

ROOT = os.path.dirname(HERE)
PUBLIC = os.path.join(ROOT, "public")
SOURCE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PUBLIC, "cameras.geojson")
OUTPUT = os.path.join(PUBLIC, "cameras.geojson")
COMPACT = os.path.join(PUBLIC, "cameras.core.json")
SUMMARY = os.path.join(PUBLIC, "summary.json")

t0 = time.time()
print(f"Source      : {SOURCE}")
print(f"Database    : {store.DEFAULT_DB}")

data = json.load(open(SOURCE, encoding="utf-8"))
features = data.get("features", [])
print(f"Features    : {len(features):,}")

conn = store.connect()
removed = store.clear_all(conn)
print(f"Cleared     : {removed:,} existing rows")

added = updated = 0
batch = []
for i, feat in enumerate(features, 1):
    if not isinstance(feat, dict):
        continue
    batch.append(feat)
    if len(batch) >= 5000:
        a, u = store.upsert_features(conn, batch, commit=False)
        added += a
        updated += u
        batch = []
        print(f"  imported {i:,}/{len(features):,}", flush=True)
if batch:
    a, u = store.upsert_features(conn, batch, commit=False)
    added += a
    updated += u

native, duplicate = store.dedupe_all(conn, commit=False)
conn.commit()
print(f"Imported    : {added:,} added, {updated:,} updated")
print(f"Dedupe      : {native:,} mirrored, {duplicate:,} duplicate feeds dropped")

total = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
print(f"Store now   : {total:,} cameras")

row = conn.execute(
    "SELECT COUNT(*) FROM cameras WHERE feed_url IS NOT NULL AND feed_url != ''"
).fetchone()[0]
stream = conn.execute(
    "SELECT COUNT(*) FROM cameras WHERE stream_url IS NOT NULL AND stream_url != ''"
).fetchone()[0]
direct = conn.execute("SELECT COUNT(*) FROM cameras WHERE direct_eligible = 1").fetchone()[0]
print(f"  feed_url  : {row:,}")
print(f"  stream_url: {stream:,}")
print(f"  direct    : {direct:,}")

print("Exporting geojson + compact + summary...")
store.export_geojson(conn, OUTPUT)
store.export_compact(conn, COMPACT)
store.export_summary(conn, SUMMARY)
conn.close()
print(f"Done in {time.time() - t0:.1f}s")
