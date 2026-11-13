"""Measure a real local consumer outage with a continuously committing producer."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import inventory_source as source
import inventory_sync as sync

NS = 1_000_000_000


def key(doc):
    return doc["tenant_id"], doc["product_id"]


def checkpoint(db):
    return [tuple(r) for r in db.execute(
        "SELECT partition_id,checkpoint FROM feed_partition ORDER BY partition_id"
    )]


def wait_until(deadline, clock, sleep):
    while (remaining := deadline - clock()) > 0:
        sleep(remaining / NS)


def digest(rows):
    ordered = [rows[k] for k in sorted(rows)]
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schedule(duration_s, interval_s):
    if not all(math.isfinite(v) and v > 0 for v in (duration_s, interval_s)):
        raise ValueError("duration and interval must be finite and positive")
    duration_ns, interval_ns = round(duration_s * NS), round(interval_s * NS)
    if min(duration_ns, interval_ns) < 1:
        raise ValueError("duration and interval must be at least one nanosecond")
    return duration_ns, interval_ns, (duration_ns + interval_ns - 1) // interval_ns


def run(path, seed, updates, *, duration_s=600, interval_s=1, clock=time.monotonic_ns, sleep=time.sleep):
    duration_ns, interval_ns, total = schedule(duration_s, interval_s)
    updates = iter(updates)
    expected = {key(d): d for d in seed}
    if len(expected) != len(seed):
        raise ValueError("Duplicate seed key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=False)
    with closing(sync.connect(path)) as writer:
        sync.append_changes(writer, seed)
        with closing(sync.connect(path)) as consumer:
            sync.drain(consumer, 0)
            baseline = sync.target_state(consumer)
            before_checkpoint = checkpoint(consumer)
        if baseline != expected:
            raise ValueError("Seed disagrees with baseline")
        start_utc = datetime.now(timezone.utc).isoformat()
        start = clock()
        observed_commits = {}
        deletes = 0
        for ordinal in range(total):
            wait_until(start + ordinal * interval_ns, clock, sleep)
            document = dict(next(updates))
            before = clock()
            document["changed_at_ms"] = (before - start) // 1_000_000
            sync.append_changes(writer, [document])
            after = clock()
            expected[key(document)] = document
            observed_commits[key(document)] = (before, after)
            deletes += int(document["deleted"])
        wait_until(start + duration_ns, clock, sleep)
        if sync.target_state(writer) != baseline or checkpoint(writer) != before_checkpoint:
            raise ValueError("Projection or checkpoint advanced while consumer closed")
        if sync.source_state(writer) != expected:
            raise ValueError("Source disagrees with independently folded mutations")
        pacing = producer_pacing(writer, total, interval_ns)
        reopened = clock()
        with closing(sync.connect(path)) as restarted:
            restart_counts = sync.drain(restarted, (clock() - start) // 1_000_000)
            drain_end = clock()
        observations = []
        with closing(sync.connect(path)) as reader:
            for (tenant, product), (before, after) in sorted(observed_commits.items()):
                row = reader.execute("SELECT * FROM inventory_current WHERE tenant_id=? AND product_id=?",
                                     (tenant, product)).fetchone()
                read_at = clock()
                wanted = expected[(tenant, product)]
                if row is None or any(row[field] != value for field, value in wanted.items()):
                    raise ValueError("Product image mismatch at first verification read")
                observations.append({
                    "tenant_id": tenant, "product_id": product, "version": row["version"],
                    "source_precommit_ns": before - start, "source_postcommit_ns": after - start,
                    "observed_read_ns": read_at - start,
                })
            actual = sync.target_state(reader)
            if actual != expected:
                raise ValueError("Recovered full product images disagree")
            if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity failure")
            if reader.execute("SELECT COUNT(*) FROM feed_partition WHERE offered_seq<>checkpoint").fetchone()[0]:
                raise ValueError("A partition still has pending changes")
    upper_ms = sorted((r["observed_read_ns"] - r["source_precommit_ns"]) / 1_000_000 for r in observations)
    return {
        "environment": "real elapsed local Python/SQLite producer and restarted consumer; single machine",
        "source_revision": source.REVISION, "started_utc": start_utc,
        "requested_outage_seconds": duration_s, "consumer_closed_seconds": round((reopened - start) / NS, 6),
        "scheduled_interval_seconds": interval_s, "producer_commits_during_outage": total,
        "observed_producer_pacing": pacing,
        "outage_soft_delete_writes": deletes, "seed_keys": len(seed), "final_keys": len(actual),
        "final_tombstones": sum(d["deleted"] for d in actual.values()), "verified_updated_keys": len(observations),
        "full_image_mismatches": 0, "checkpoints_unchanged_during_outage": True, "all_checkpoints_caught_up": True,
        "consumer_reopen_and_drain_ms": round((drain_end - reopened) / 1_000_000, 3),
        "p95_first_read_upper_bound_ms": round(upper_ms[math.ceil(0.95 * len(upper_ms)) - 1], 3),
        "restart_decisions": restart_counts, "full_state_sha256": digest(actual), "cloud_deployed": False,
    }, observations


def producer_pacing(db, total, interval_ns):
    starts_ms = [
        json.loads(payload)["changed_at_ms"]
        for (payload,) in db.execute(
            "SELECT payload FROM source_log WHERE admission='changed' AND version>1 ORDER BY seq"
        )
    ]
    if len(starts_ms) != total:
        raise ValueError("Producer submission count does not match its schedule")
    gaps = [b - a for a, b in zip(starts_ms, starts_ms[1:])]
    return {
        "first_submit_ms": starts_ms[0], "last_submit_ms": starts_ms[-1],
        "max_inter_submit_gap_ms": max(gaps, default=0),
        "max_scheduled_submit_delay_ms": max(t - (i * interval_ns) // 1_000_000 for i, t in enumerate(starts_ms)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, required=True)
    ap.add_argument("--source-dir", type=Path, default=Path("data/source"))
    ap.add_argument("--duration-seconds", type=int, default=600)
    ap.add_argument("--interval-seconds", type=float, default=1)
    ap.add_argument("--observations", type=Path, required=True)
    args = ap.parse_args()
    _, _, count = schedule(args.duration_seconds, args.interval_seconds)
    seed = source.seed_documents(source.load_products(args.source_dir))
    if args.database.resolve() == args.observations.resolve() or args.observations.exists():
        raise ValueError("Provide separate fresh database and observation paths")
    report, rows = run(args.database, seed, source.mutations(seed, count),
                       duration_s=args.duration_seconds, interval_s=args.interval_seconds)
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    with args.observations.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    report["observations_sha256"] = hashlib.sha256(args.observations.read_bytes()).hexdigest()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
