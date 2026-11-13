"""Credential-free latest-version inventory feed and transactional SQLite projection."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

FIELDS = ("tenant_id", "product_id", "sku", "stock", "version", "deleted", "changed_at_ms", "parent_id")
TEXT_FIELDS = ("tenant_id", "product_id", "sku", "parent_id")
MAX_INT = 2**63 - 1
LOGGER = logging.getLogger(__name__)


def connect(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    schema = Path(__file__).parent / "sql" / "local_schema.sql"
    db.executescript(schema.read_text(encoding="utf-8"))
    return db


@contextmanager
def _transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _integer(value, minimum):
    return type(value) is int and minimum <= value <= MAX_INT


def _valid_text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        return len(value.encode("utf-16le")) <= 256
    except UnicodeEncodeError:
        return False


def _validation_error(doc):
    if not isinstance(doc, dict) or doc.keys() != set(FIELDS):
        return "document fields differ from schema"
    for name in TEXT_FIELDS:
        if not _valid_text(doc[name]):
            return f"{name} must be trimmed text of 1 to 128 UTF-16 code units"
    for name in ("stock", "version", "changed_at_ms"):
        if not _integer(doc[name], int(name == "version")):
            return f"{name} outside integer domain"
    if type(doc["deleted"]) is not bool:
        return "deleted must be boolean"
    return None


def _admit(db, doc, partition, payload):
    error = _validation_error(doc)
    if error:
        return "invalid", error
    key = (doc["tenant_id"], doc["product_id"])
    ver = doc["version"]
    prior = db.execute(
        """SELECT payload FROM source_log WHERE partition_id=? AND product_id=? AND version=?
           AND admission IN ('changed','stale') ORDER BY seq LIMIT 1""",
        (partition, key[1], ver),
    ).fetchone()
    if prior:
        if prior["payload"] != payload:
            return "conflict", "same version has different content"
        return "duplicate", None
    current = db.execute("SELECT version FROM source_head WHERE tenant_id=? AND product_id=?", key).fetchone()
    if current and ver < current["version"]:
        return "stale", None
    return "changed", None


def append_changes(db, documents):
    """Store one atomic batch of JSON-compatible source proposals with their provenance."""
    count = 0
    with _transaction(db):
        for doc in documents:
            payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)
            tenant = doc.get("tenant_id") if isinstance(doc, dict) else None
            partition = "t:" + tenant if _valid_text(tenant) else "invalid"
            admission, reason = _admit(db, doc, partition, payload)
            db.execute("INSERT OR IGNORE INTO feed_partition(partition_id,offered_seq) VALUES (?,0)", (partition,))
            identity = (doc["product_id"], doc["version"]) if admission != "invalid" else (None, None)
            seq = db.execute(
                """INSERT INTO source_log(partition_id,product_id,version,payload,sha256,admission,reason)
                   VALUES (?,?,?,?,?,?,?)""",
                (partition, *identity, payload, hashlib.sha256(payload.encode()).hexdigest(), admission, reason),
            ).lastrowid
            if admission == "changed":
                db.execute(
                    """INSERT INTO source_head VALUES (?,?,?,?)
                       ON CONFLICT(tenant_id,product_id) DO UPDATE SET
                       version=excluded.version,source_seq=excluded.source_seq
                       WHERE excluded.version > source_head.version""",
                    (tenant, *identity, seq),
                )
            db.execute("UPDATE feed_partition SET offered_seq=? WHERE partition_id=?", (seq, partition))
            count += 1
    return count


def _quarantine(db, row, reason, clock):
    db.execute(
        "INSERT INTO quarantine VALUES (?,?,?) ON CONFLICT(source_seq) DO NOTHING",
        (row["seq"], reason, clock),
    )
    return "quarantined"


def _project(db, row, clock):
    doc = json.loads(row["payload"])
    target = db.execute(
        """SELECT c.version,l.payload FROM inventory_current c JOIN source_log l ON c.source_seq=l.seq
           WHERE c.tenant_id=? AND c.product_id=?""",
        (doc["tenant_id"], doc["product_id"]),
    ).fetchone()
    if target and doc["version"] <= target["version"]:
        if doc["version"] < target["version"]:
            return "stale"
        if row["payload"] != target["payload"]:
            return _quarantine(db, row, "target version has different content", clock)
        return "duplicate"
    if clock < doc["changed_at_ms"]:
        raise ValueError("visibility clock precedes source clock")
    db.execute(
        """INSERT INTO inventory_current VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(tenant_id,product_id) DO UPDATE SET
           sku=excluded.sku,stock=excluded.stock,version=excluded.version,deleted=excluded.deleted,
           changed_at_ms=excluded.changed_at_ms,parent_id=excluded.parent_id,source_seq=excluded.source_seq
           WHERE excluded.version > inventory_current.version""",
        tuple(doc[name] for name in FIELDS) + (row["seq"],),
    )
    db.execute(
        """INSERT INTO projection_audit(source_seq,tenant_id,product_id,version,changed_at_ms,visible_at_ms)
           VALUES (?,?,?,?,?,?)""",
        (row["seq"], doc["tenant_id"], doc["product_id"], doc["version"], doc["changed_at_ms"], clock),
    )
    return "applied"


def _apply_page(db, rows, clock, counts):
    # The local feed may omit earlier versions of an item still waiting on the same page.
    latest = {r["product_id"]: r["seq"] for r in rows if r["admission"] == "changed"}
    for row in rows:
        decision = row["admission"]
        if decision in ("invalid", "conflict"):
            decision = _quarantine(db, row, row["reason"], clock)
        elif decision == "changed":
            if row["seq"] != latest[row["product_id"]]:
                decision = "coalesced"
            else:
                decision = _project(db, row, clock)
        counts[decision] += 1


def drain(db, visible_at_ms, batch_size=256):
    """Project pages to the per-partition high watermarks observed when this call starts."""
    if not _integer(visible_at_ms, 0) or not _integer(batch_size, 1):
        raise ValueError("invalid visibility clock or batch size")
    counts = dict.fromkeys(("applied", "duplicate", "stale", "quarantined", "coalesced"), 0)
    pending = db.execute(
        "SELECT partition_id,offered_seq FROM feed_partition WHERE checkpoint < offered_seq ORDER BY partition_id"
    ).fetchall()
    for partition, limit in pending:
        while True:
            with _transaction(db):
                rows = db.execute(
                    """SELECT l.* FROM source_log l WHERE partition_id=? AND seq >
                       (SELECT checkpoint FROM feed_partition WHERE partition_id=?) AND seq<=?
                       ORDER BY seq LIMIT ?""",
                    (partition, partition, limit, batch_size),
                ).fetchall()
                if not rows:
                    break
                _apply_page(db, rows, visible_at_ms, counts)
                db.execute("UPDATE feed_partition SET checkpoint=? WHERE partition_id=?", (rows[-1]["seq"], partition))
            if rows[-1]["seq"] >= limit:
                break
    LOGGER.info("inventory feed drain: %s", counts)
    return counts


def redeliver(db, source_seq, visible_at_ms):
    """Repeat a committed source delivery independently of its normal feed checkpoint."""
    if not _integer(visible_at_ms, 0):
        raise ValueError("invalid visibility clock")
    with _transaction(db):
        row = db.execute(
            """SELECT l.* FROM source_log l JOIN feed_partition p USING(partition_id)
               WHERE l.seq=? AND l.seq<=p.checkpoint""",
            (source_seq,),
        ).fetchone()
        if not row:
            raise KeyError(source_seq)
        if row["admission"] in ("invalid", "conflict"):
            return _quarantine(db, row, row["reason"], visible_at_ms)
        if row["admission"] == "stale":
            return "stale"
        return _project(db, row, visible_at_ms)


def source_state(db):
    rows = db.execute("SELECT l.payload FROM source_head h JOIN source_log l ON h.source_seq=l.seq")
    docs = (json.loads(row["payload"]) for row in rows)
    return {(doc["tenant_id"], doc["product_id"]): doc for doc in docs}


def target_state(db):
    result = {}
    for row in db.execute("SELECT * FROM inventory_current"):
        doc = {name: row[name] for name in FIELDS}
        doc["deleted"] = bool(doc["deleted"])
        result[(doc["tenant_id"], doc["product_id"])] = doc
    return result


def visibility_latencies(db):
    return [row[0] for row in db.execute("SELECT latency_ms FROM projection_audit ORDER BY source_seq")]
