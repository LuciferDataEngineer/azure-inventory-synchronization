"""Run the inventory projection drill, export its workload or time a local observation."""
import argparse
from contextlib import closing
import itertools
import json
import logging
import math
import os
import sqlite3
from pathlib import Path
import tempfile
import time

import inventory_source as source
import inventory_sync as sync


LOG = logging.getLogger(__name__)
POLL_MS = 15_000
OUTAGE_START_MS = 1_200_000
OUTAGE_END_MS = OUTAGE_START_MS + 600_000
TARGET_MS = 60_000


def key(doc):
    return doc["tenant_id"], doc["product_id"]


def mismatch_count(expected, observed):
    return sum(expected.get(k) != observed.get(k) for k in expected.keys() | observed.keys())


def p95(values):
    """Nearest rank, calculated over observations that actually became visible."""
    if not values:
        return None
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def new_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.touch(exist_ok=False)
    except FileExistsError as err:
        raise ValueError(f"{path} exists. Inspect it or use a new database path.") from err
    try:
        return sync.connect(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def write_rows(path, rows):
    """Commit the output only after the input stream finishes successfully."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as f:
        tmp = Path(f.name)
        try:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def drill(path, seed, updates):
    expected = {key(doc): doc for doc in seed}
    check(len(seed) == len(expected), "Duplicate seed key")
    db = new_db(path)
    last_time = 0
    outage_writes = outage_deletes = 0
    pending = []
    recovered = None
    normal_lags = []
    observed_lags = []

    def poll(now, recovery=False):
        # An item can pass through several committed versions during catch-up.
        # Count every applied version from the transaction audit, including those
        # already superseded by the time the final current-state view is read.
        sync.drain(db, now)
        actual = sync.target_state(db)
        check(mismatch_count(expected, actual) == 0, f"Projection differs at {now} ms")
        lags = [row[0] for row in db.execute(
            "SELECT latency_ms FROM projection_audit WHERE visible_at_ms = ? AND version > 1", (now,)
        )]
        observed_lags.extend(lags)
        if not recovery:
            normal_lags.extend(lags)
        return len(lags)

    try:
        sync.append_changes(db, seed)
        sync.drain(db, 0)
        check(sync.target_state(db) == expected, "Seed projection differs")
        pre_outage_snapshot = None
        next_poll = POLL_MS
        count = 0

        def boundary(at):
            nonlocal db, recovered, pre_outage_snapshot
            if pending:
                sync.append_changes(db, pending)
                pending.clear()
            if at == OUTAGE_START_MS:
                pre_outage_snapshot = sync.target_state(db)
                LOG.info("Consumer paused at %s ms", at)
            if OUTAGE_START_MS <= at < OUTAGE_END_MS:
                return
            if at == OUTAGE_END_MS:
                check(pre_outage_snapshot is not None, "Outage start was not reached")
                check(sync.target_state(db) == pre_outage_snapshot, "Consumer advanced during outage")
                db.close()
                db = sync.connect(path)
                projected = poll(at, recovery=True)
                recovered = {"applied_versions": projected, "mismatches": 0}
                LOG.info("Consumer resumed from disk at %s ms", at)
                return
            poll(at)

        for doc in updates:
            now = doc["changed_at_ms"]
            check(now > last_time, "Workload mutations must have strictly increasing timestamps")
            while next_poll < now:
                boundary(next_poll)
                next_poll += POLL_MS
            k = key(doc)
            check(k in expected and doc["version"] > expected[k]["version"], "Mutation must advance an existing product")
            if OUTAGE_START_MS <= now < OUTAGE_END_MS:
                outage_writes += 1
                outage_deletes += int(doc["deleted"] and not expected[k]["deleted"])
            expected[k] = doc
            pending.append(doc)
            count += 1
            if now == next_poll:
                boundary(next_poll)
                next_poll += POLL_MS
            last_time = now
        if pending:
            boundary(next_poll)
        check(recovered is not None and outage_deletes > 0, "Workload did not exercise outage recovery with soft deletes")
        check(all(delay >= 0 for delay in observed_lags), "Projection preceded a source write")
        final = sync.target_state(db)
        check(mismatch_count(expected, sync.source_state(db)) == 0, "Source differs from independent workload")
        final_errors = mismatch_count(expected, final)
        check(final_errors == 0, "Final projection differs from independent workload")
        check(any(doc["deleted"] for doc in final.values()), "Missing final tombstones")
        # Deliver the same version and then a stale version straight to the sink.
        stale_doc = next(doc for doc in seed if expected[key(doc)]["version"] > doc["version"])
        latest = expected[key(stale_doc)]
        for replay, expected_outcome in ((latest, "duplicate"), (stale_doc, "stale")):
            seq = db.execute(
                "SELECT seq FROM source_log WHERE partition_id=? AND product_id=? AND version=? AND admission='changed'",
                ("t:" + replay["tenant_id"], replay["product_id"], replay["version"])
            ).fetchone()[0]
            check(sync.redeliver(db, seq, next_poll + POLL_MS) == expected_outcome, "Unexpected redelivery result")
            check(sync.target_state(db) == expected, "Redelivery changed current state")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        check(integrity == "ok", "SQLite integrity check failed")
        return {
            "coverage": "synthetic inventory workload on a simulated clock",
            "seed_products": len(seed),
            "mutations": count,
            "tenants": len({k[0] for k in expected}),
            "poll_interval_ms": POLL_MS,
            "outage_duration_seconds": (OUTAGE_END_MS - OUTAGE_START_MS) // 1000,
            "outage_mutations": outage_writes,
            "outage_soft_deletes": outage_deletes,
            "at_recovery": recovered,
            "final_products": len(final),
            "final_tombstones": sum(doc["deleted"] for doc in final.values()),
            "final_state_mismatches": final_errors,
            "replay_checks": 2,
            "sqlite_integrity": integrity,
            "visible_mutation_versions": len(observed_lags),
            "normal_visible_versions": len(normal_lags),
            "simulated_p95_normal_ms": p95(normal_lags),
            "simulated_p95_including_recovery_ms": p95(observed_lags),
            "simulated_max_lag_ms": max(observed_lags),
            "visible_versions_over_60s": sum(delay > TARGET_MS for delay in observed_lags),
        }
    finally:
        db.close()


def local_timing(path, seed, updates, limit=1000, batch_size=10):
    """Wall-clock source-submit to post-commit SQL readback; a separate local timing trial."""
    check(limit > 0 and batch_size > 0, "Positive trial size required")
    elapsed = []
    it = itertools.islice(updates, limit)
    delivered = 0
    read_only_uri = path.resolve().as_uri() + "?mode=ro"
    with closing(new_db(path)) as db, closing(sqlite3.connect(read_only_uri, uri=True)) as reader:
        reader.row_factory = sqlite3.Row
        sync.append_changes(db, seed)
        sync.drain(db, 0)
        while batch := list(itertools.islice(it, batch_size)):
            want = {key(doc): doc for doc in batch}
            start = time.perf_counter_ns()
            sync.append_changes(db, batch)
            sync.drain(db, batch[-1]["changed_at_ms"])
            view = sync.target_state(reader)
            end = time.perf_counter_ns()
            check(all(view[k] == doc for k, doc in want.items()), "Timed SQL readback differs")
            elapsed.extend([(end - start) / 1_000_000] * len(want))
            delivered += len(batch)
    check(delivered == limit, "Timing trial received fewer mutations than requested")
    return {
        "scope": "local SQLite wall time, includes full target readback",
        "submitted_mutations": delivered,
        "visible_versions": len(elapsed),
        "batch_size": batch_size,
        "p95_ms": round(p95(elapsed), 3),
        "max_ms": round(max(elapsed), 3),
        "clock": "perf_counter_ns",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest="action")
    for name in ("drill", "time", "export"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--source-dir", type=Path, default=Path("data/source"))
        if name == "export":
            cmd.add_argument("--output", type=Path, required=True)
        else:
            cmd.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    products = source.load_products(args.source_dir)
    seed = list(source.seed_documents(products))
    updates = source.mutations(seed)
    if args.action == "export":
        write_rows(args.output, itertools.chain(seed, updates))
        LOG.info("Wrote %s", args.output)
    else:
        report = drill(args.db, seed, updates) if args.action == "drill" else local_timing(args.db, seed, updates)
        print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
