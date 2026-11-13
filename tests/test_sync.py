import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from inventory_sync import append_changes, connect, drain, redeliver, source_state, target_state, visibility_latencies


def product(product_id="a", version=1, changed_at_ms=100, **overrides):
    return dict(
        {"tenant_id": "north", "product_id": product_id, "sku": "sample", "stock": 12,
         "version": version, "deleted": False, "changed_at_ms": changed_at_ms, "parent_id": "base-11"},
        **overrides,
    )


class EngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "inventory.sqlite"
        self.open()

    def open(self):
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    def test_incremental_pages_coalesce_without_crossing_tenants(self):
        a1, a2, b1 = product(), product(version=2), product(tenant_id="south")
        self.assertEqual(3, append_changes(self.db, [a1, b1, a2]))
        self.assertEqual(2, len(source_state(self.db)))
        result = drain(self.db, 140)
        self.assertEqual((2, 1), (result["applied"], result["coalesced"]))
        self.assertEqual(source_state(self.db), target_state(self.db))
        self.assertEqual([40, 40], visibility_latencies(self.db))
        self.assertEqual(0, sum(drain(self.db, 300).values()))

    def test_no_history_guarantee_for_intermediate_tombstone(self):
        append_changes(self.db, [product(), product(version=2, deleted=True), product(version=3)])
        result = drain(self.db, 100, batch_size=3)
        self.assertEqual((1, 2), (result["applied"], result["coalesced"]))
        self.assertFalse(target_state(self.db)[("north", "a")]["deleted"])
        self.assertEqual(3, self.db.execute("SELECT version FROM projection_audit").fetchone()[0])

    def test_duplicate_stale_conflict_and_delete_do_not_regress_state(self):
        doc, tombstone = product(), product(version=4, deleted=True, stock=0)
        append_changes(self.db, [doc])
        drain(self.db, 110)
        append_changes(self.db, [tombstone, product(version=2), doc, product(version=4, stock=5)])
        outcome = drain(self.db, 200)
        self.assertEqual((1, 1, 1, 1), tuple(outcome[k] for k in ("applied", "duplicate", "stale", "quarantined")))
        self.assertEqual(tombstone, target_state(self.db)[("north", "a")])
        self.assertEqual("stale", redeliver(self.db, 1, 300))
        self.assertEqual("duplicate", redeliver(self.db, 2, 300))
        self.assertEqual("quarantined", redeliver(self.db, 5, 300))
        self.assertEqual(1, self.db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0])
        self.assertEqual([10, 100], visibility_latencies(self.db))

    def test_invalid_json_documents_quarantine_with_provenance(self):
        missing = product()
        del missing["deleted"]
        invalid = [None, ["bad"], missing, product(stock=1.1), product(version=True), product(tenant_id=""),
                   product(deleted=0), product(stock=-1), product(stock=2**65), product(changed_at_ms=-1),
                   product(extra=2), product(parent_id=" "), product(tenant_id=chr(0xd800)),
                   product(sku="x" * 129), product(product_id="trailing-space ")]
        append_changes(self.db, invalid + [product()])
        result = drain(self.db, 100)
        self.assertEqual(len(invalid), result["quarantined"])
        self.assertEqual(1, result["applied"])
        rows = self.db.execute("SELECT payload,sha256,q.reason FROM quarantine q JOIN source_log l ON l.seq=q.source_seq")
        for payload, digest, reason in rows:
            self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), digest)
            self.assertTrue(reason)
        self.assertEqual(source_state(self.db), target_state(self.db))

    def test_non_json_batch_rolls_back_all_source_writes(self):
        with self.assertRaises(ValueError):
            append_changes(self.db, [product(), product(stock=float("nan"))])
        self.assertEqual(0, self.db.execute("SELECT COUNT(*) FROM source_log").fetchone()[0])
        self.assertEqual({}, source_state(self.db))

    def test_sink_failure_rolls_back_page_projection_quarantine_and_checkpoint(self):
        append_changes(self.db, [product("x", stock=-1), product(), product("b")])
        self.db.execute("""CREATE TRIGGER fail_test BEFORE INSERT ON projection_audit
                           WHEN NEW.product_id='b' BEGIN SELECT RAISE(ABORT,'test fault'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test fault"):
            drain(self.db, 200)
        self.assertEqual({}, target_state(self.db))
        self.assertEqual([], visibility_latencies(self.db))
        self.assertEqual(0, self.db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0])
        self.assertEqual(0, self.db.execute("SELECT checkpoint FROM feed_partition").fetchone()[0])
        self.db.execute("DROP TRIGGER fail_test")
        self.assertEqual(2, drain(self.db, 600)["applied"])
        self.assertEqual(source_state(self.db), target_state(self.db))

    def test_restart_from_last_committed_page(self):
        append_changes(self.db, [product("a"), product("b"), product("c", changed_at_ms=200)])
        with self.assertRaisesRegex(ValueError, "clock precedes"):
            drain(self.db, 100, batch_size=2)
        self.assertEqual(2, len(target_state(self.db)))
        self.db.close()
        self.open()
        self.assertEqual(1, drain(self.db, 220)["applied"])
        self.assertEqual([0, 0, 20], visibility_latencies(self.db))
        self.assertEqual(source_state(self.db), target_state(self.db))

    def test_competing_workers_once_and_rewind_safe(self):
        append_changes(self.db, [product(str(i)) for i in range(96)])
        def worker():
            db = connect(self.path)
            try:
                return drain(db, 120, batch_size=3)["applied"]
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(96, sum(results))
        self.db.execute("UPDATE feed_partition SET checkpoint=0")
        self.assertEqual(96, drain(self.db, 300, batch_size=1)["duplicate"])
        self.assertEqual([20] * 96, visibility_latencies(self.db))

    def test_preconditions_for_visibility_and_replay(self):
        append_changes(self.db, [product()])
        for clock, batch in [(False, 10), (100, -1), (-1, 10)]:
            with self.assertRaises(ValueError):
                drain(self.db, clock, batch_size=batch)
        with self.assertRaises(KeyError):
            redeliver(self.db, 1, 100)
        drain(self.db, 100)
        with self.assertRaises(KeyError):
            redeliver(self.db, 999, 100)
