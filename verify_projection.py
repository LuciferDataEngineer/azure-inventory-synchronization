"""Independently check a completed deterministic drill against its original workload."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def readback(db, query, params=()):
    images = {}
    for row in db.execute(query, params):
        image = dict(row)
        image["deleted"] = bool(image["deleted"])
        images[(image["tenant_id"], image["product_id"])] = image
    return images


def same(label, actual, expected):
    if actual != expected:
        raise ValueError(f"{label} differs from the exported workload")


def verify(db_path, export_path, recovery_ms=1_800_000):
    live, recovered = {}, {}
    total = 0
    with export_path.open(encoding="utf8") as stream:
        for line in stream:
            doc = json.loads(line)
            key = (doc["tenant_id"], doc["product_id"])
            if key in live and live[key]["version"] >= doc["version"]:
                raise ValueError(f"Workload versions must increase at input row {total+1}")
            live[key] = doc
            if doc["changed_at_ms"] <= recovery_ms:
                recovered[key] = doc
            total += 1
    fields = "tenant_id, product_id, sku, stock, version, deleted, changed_at_ms, parent_id"
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        same("Final product images", readback(db, f"SELECT {fields} FROM inventory_current"), live)
        prior = {}
        for row in db.execute(
            """SELECT l.payload FROM projection_audit a JOIN source_log l ON l.seq = a.source_seq
            WHERE a.visible_at_ms <= ? ORDER BY a.visible_at_ms, a.source_seq""", (recovery_ms,)
        ):
            doc = json.loads(row["payload"])
            prior[(doc["tenant_id"], doc["product_id"])] = doc
        same("Recovered product images", prior, recovered)
        # Compare the raw journal to the exported workload in submission order.
        rows = iter(db.execute("SELECT payload, sha256 FROM source_log ORDER BY seq"))
        with export_path.open("rb") as stream:
            for ordinal, line in enumerate(stream, start=1):
                row = next(rows, None)
                exact = line.rstrip(b"\r\n")
                if row is None or row["payload"].encode() != exact or row["sha256"] != hashlib.sha256(exact).hexdigest():
                    raise ValueError(f"Raw provenance differs at input row {ordinal}")
        same("Raw journal length", next(rows, None), None)
        same("Unconsumed partitions", db.execute("SELECT COUNT(*) FROM feed_partition WHERE offered_seq != checkpoint").fetchone()[0], 0)
        same("SQLite integrity", db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        applied = db.execute("SELECT COUNT(*) FROM projection_audit WHERE version>1").fetchone()[0]
    active_by_tenant = Counter(doc["tenant_id"] for doc in live.values() if not doc["deleted"])
    return {"checked_input_rows": total, "checked_final_product_images": len(live),
            "checked_recovery_product_images": len(prior), "tombstones": len(live) - sum(active_by_tenant.values()),
            "applied_mutation_versions": applied, "unexplained_mismatches": 0}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("db", type=Path)
    cli.add_argument("workload", type=Path)
    args = cli.parse_args()
    print(json.dumps(verify(args.db, args.workload), sort_keys=True, indent=2))
