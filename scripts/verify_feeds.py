#!/usr/bin/env python3
"""Continuously verify that cameras actually open, and hide the dead ones.

A feed is dead in practice when the URL behind the pin fails to deliver an
image/stream/player. Listing it anyway is what makes the map feel broken, so
this script walks the store, probes each feed with the same logic the app uses,
and records the result in the database:

  * success  -> `last_seen` refreshed, failure streak reset, camera re-activated
  * failure  -> streak +1; after `--deactivate-after` consecutive failures the
                camera gets `deactivated_at`, and every export stops publishing
                it (it stays in the DB and comes back automatically if a later
                check succeeds)

The walk is resumable and fair: live streams are checked first, then the feeds
that have gone longest without a check, so repeated runs cover the whole store
without re-probing the same cameras.

Usage:
    python scripts/verify_feeds.py                # full sweep, resumable
    python scripts/verify_feeds.py --limit 2000   # quick slice
    python scripts/verify_feeds.py --export       # re-publish payloads after
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import health  # noqa: E402
import store   # noqa: E402

ROOT = os.path.dirname(HERE)
PUBLIC = os.path.join(ROOT, "public")
PROXY = os.getenv("NEXUS_PROXY_BASE", "http://127.0.0.1:8787/api/proxy?url=")
OUT = os.path.join(HERE, "data")


def select_targets(conn, limit, hours):
    """Cameras to check, live first and stalest first."""
    where = ["(stream_url != '' OR direct_eligible = 1)"]
    params = []
    if hours > 0:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(time.time() - hours * 3600))
        where.append("(last_checked IS NULL OR last_checked < ?)")
        params.append(cutoff)
    sql = (
        "SELECT id, stream_url, feed_url, feed_type FROM cameras WHERE "
        + " AND ".join(where)
        # Live streams first (they are what users click), then the feeds that
        # waited longest; RANDOM() breaks ties so a slice is not one host.
        + " ORDER BY (stream_url = '') ASC, last_checked IS NOT NULL ASC,"
          " last_checked ASC, RANDOM()"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max cameras this run")
    ap.add_argument("--hours", type=float, default=0,
                    help="only re-check cameras not checked in N hours")
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--deactivate-after", type=int, default=3)
    ap.add_argument("--export", action="store_true",
                    help="re-export geojson/compact/summary when finished")
    ap.add_argument("--offline", action="store_true",
                    help="skip the control probe (only for tests)")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    conn = store.connect()
    targets = select_targets(conn, args.limit, args.hours)
    if not targets:
        print("Nothing to check: every camera was checked recently.")
        return

    if not args.offline and not health.network_ok():
        print("[ABORT] control feeds did not answer — the local connection is "
              "failing, so no camera is marked dead this run.")
        return

    host_sem, proxy_sem = health.make_limiters()
    total = len(targets)
    started = time.time()
    batch, done, ok_count = [], 0, 0
    jsonl = open(os.path.join(OUT, "verify_log.jsonl"), "a", encoding="utf-8")

    def work(row):
        url = (row["stream_url"] or "").strip() or (row["feed_url"] or "").strip()
        res = health.probe(url, row["feed_type"] or "", PROXY,
                           host_sem=host_sem, proxy_sem=proxy_sem)
        res["id"] = row["id"]
        res["url"] = url[:300]
        return res

    print(f"Checking {total:,} cameras with {args.workers} workers...", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, row) for row in targets]
        for fut in as_completed(futures):
            res = fut.result()
            batch.append(res)
            jsonl.write(json.dumps(res, ensure_ascii=False) + "\n")
            done += 1
            ok_count += 1 if res["ok"] else 0
            if len(batch) >= 250:
                store.record_health(conn, batch, deactivate_after=args.deactivate_after)
                jsonl.flush()
                batch = []
                rate = 100 * ok_count / done
                print(f"  {done:,}/{total:,}  ok={rate:4.1f}%  "
                      f"{time.time() - started:5.0f}s", flush=True)
    if batch:
        store.record_health(conn, batch, deactivate_after=args.deactivate_after)
    jsonl.close()

    dead = conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE deactivated_at IS NOT NULL").fetchone()[0]
    total_all = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    summary = {
        "checked": done,
        "ok": ok_count,
        "failed": done - ok_count,
        "ok_rate_pct": round(100 * ok_count / done, 2) if done else None,
        "deactivated_total": dead,
        "store_total": total_all,
        "elapsed_seconds": round(time.time() - started, 1),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    json.dump(summary, open(os.path.join(OUT, "verify_summary.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.export:
        print("Re-exporting payloads...")
        store.export_geojson(conn, os.path.join(PUBLIC, "cameras.geojson"))
        store.export_compact(conn, os.path.join(PUBLIC, "cameras.core.json"))
        store.export_summary(conn, os.path.join(PUBLIC, "summary.json"))
        print("Done.")
    conn.close()


if __name__ == "__main__":
    main()
