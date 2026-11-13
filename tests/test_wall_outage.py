import tempfile
import unittest
from pathlib import Path

from wall_outage import run


class TestClock:
    def __init__(self):
        self.nanoseconds = 0
        self.waits = []

    def read(self):
        return self.nanoseconds

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.nanoseconds += round(seconds * 1_000_000_000)


def sample(product, **overrides):
    return dict({
        "tenant_id": "lab", "product_id": product, "sku": "fixture-" + product,
        "stock": 2, "deleted": False, "changed_at_ms": 0, "version": 1, "parent_id": product,
    }, **overrides)


class WallOutageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "drill.sqlite"
        self.clock = TestClock()
        self.seed = [sample("p1"), sample("p2")]

    def simulate(self, updates, duration=3):
        return run(self.path, self.seed, updates, duration_s=duration, interval_s=1,
                   clock=self.clock.read, sleep=self.clock.sleep)

    def test_closed_consumer_committed_updates_and_read_after_reopen(self):
        updates = [sample("p1", version=2, stock=1), sample("p2", version=2, stock=0, deleted=True),
                   sample("p1", version=3, stock=5)]
        result, rows = self.simulate(updates)
        self.assertEqual((3, 3, 2, 1, 0), tuple(result[k] for k in (
            "consumer_closed_seconds", "producer_commits_during_outage", "verified_updated_keys",
            "final_tombstones", "full_image_mismatches",
        )))
        self.assertEqual([1.0, 1.0, 1.0], self.clock.waits)
        self.assertTrue(result["checkpoints_unchanged_during_outage"])
        self.assertTrue(result["all_checkpoints_caught_up"])
        self.assertEqual((2, 1), (result["restart_decisions"]["applied"], result["restart_decisions"]["coalesced"]))
        self.assertEqual(2000, result["p95_first_read_upper_bound_ms"])
        self.assertEqual({"first_submit_ms": 0, "last_submit_ms": 2000, "max_inter_submit_gap_ms": 1000,
                          "max_scheduled_submit_delay_ms": 0}, result["observed_producer_pacing"])
        self.assertEqual([("p1", 3), ("p2", 2)], [(r["product_id"], r["version"]) for r in rows])
        self.assertTrue(all(r["source_postcommit_ns"] <= r["observed_read_ns"] for r in rows))

    def test_invalid_workload_fails_reconciliation(self):
        # The independent producer thinks it replaced version one. The source rejects that conflict.
        with self.assertRaisesRegex(ValueError, "Source disagrees"):
            self.simulate([sample("p1", stock=1)], duration=1)

    def test_existing_database_is_preserved(self):
        self.path.write_bytes(b"sentinel")
        with self.assertRaises(FileExistsError):
            self.simulate([sample("p1", version=2)], duration=1)
        self.assertEqual(b"sentinel", self.path.read_bytes())
