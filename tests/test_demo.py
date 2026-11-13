import itertools
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from inventory_demo import drill, local_timing, p95, write_rows
from inventory_source import mutations, seed_documents
from verify_projection import verify


def synthetic_products():
    return [{"tenantId": str(i % 3), "id": f"test-{i:03d}", "sku": str(i), "inventory": i} for i in range(100)]


class DemoTests(unittest.TestCase):
    def test_end_to_end_restart_over_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = seed_documents(synthetic_products())
            path = Path(tmp)/"test.sqlite"
            workload = Path(tmp)/"workload.jsonl"
            write_rows(workload, itertools.chain(seed, mutations(seed, 1802)))
            report = drill(path, seed, mutations(seed, 1802))
            audit = verify(path, workload)
            self.assertEqual(audit["checked_input_rows"], 2802)
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE inventory_current SET stock=stock+1")
                db.commit()
            with self.assertRaisesRegex(ValueError, "Final product images"):
                verify(path, workload)
        self.assertEqual(report["mutations"], 1802)
        self.assertEqual(report["outage_mutations"], 600)
        self.assertGreater(report["outage_soft_deletes"], 0)
        self.assertGreater(report["visible_versions_over_60s"], 0)
        self.assertEqual(report["final_state_mismatches"], 0)
        self.assertEqual(report["at_recovery"]["mismatches"], 0)
        self.assertEqual(report["replay_checks"], 2)

    def test_timed_readback_and_existing_database_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"test.sqlite"
            seed = seed_documents(synthetic_products())
            result = local_timing(path, seed, mutations(seed, 20), limit=20, batch_size=3)
            self.assertEqual(result["visible_versions"], 20)
            self.assertGreaterEqual(result["p95_ms"], 0)
            with self.assertRaisesRegex(ValueError, "exists"):
                local_timing(path, seed, iter(()))

    def test_export_failure_preserves_previous_workload(self):
        def incomplete():
            yield {"stock": 1}
            raise RuntimeError("upstream failed")
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"workload.jsonl"
            write_rows(path, [{"stock": 7}])
            with self.assertRaises(RuntimeError):
                write_rows(path, incomplete())
            self.assertEqual(json.loads(path.read_text()), {"stock": 7})
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_nearest_rank(self):
        self.assertEqual(p95(range(1, 21)), 19)
        self.assertIsNone(p95([]))
