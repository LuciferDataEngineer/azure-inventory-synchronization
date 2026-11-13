import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import inventory_source as source


def sample_products():
    return [
        {"tenantId": f"shop-{i % 3}", "id": f"item-{i}", "sku": f"sku-{i}", "inventory": i}
        for i in range(100)
    ]


class SourceTests(unittest.TestCase):
    def test_seed_preserves_originals_and_tracks_synthetic_keys(self):
        products = sample_products()
        seed = source.seed_documents(products)
        self.assertEqual(seed, source.seed_documents(products[::-1]))
        self.assertEqual(len({(d["tenant_id"], d["product_id"]) for d in seed}), 1000)
        self.assertEqual(len([d for d in seed if d["parent_id"] == d["product_id"]]), 100)
        expected = {(p["tenantId"], p["id"]): (p["sku"], p["inventory"]) for p in products}
        self.assertEqual({(d["tenant_id"], d["product_id"]): (d["sku"], d["stock"]) for d in seed[:100]}, expected)
        self.assertTrue(all((d["tenant_id"], d["parent_id"]) in expected for d in seed))

    def test_rejects_invalid_inventory_and_duplicate_keys(self):
        for invalid in (True, -1, 4.2):
            products = sample_products()
            products[0]["inventory"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                source.seed_documents(products)
        products = sample_products()
        products[1] = products[0]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            source.seed_documents(products)

    def test_mutations_are_ordered_reproducible_full_images(self):
        seed = source.seed_documents(sample_products())
        before = [dict(r) for r in seed]
        events = list(source.mutations(seed))
        self.assertEqual(events, list(source.mutations(reversed(seed))))
        self.assertEqual(seed, before)
        self.assertEqual(len(events), 10000)
        previous = {(d["tenant_id"], d["product_id"]): d for d in seed}
        deleted_keys = set()
        changed_keys = set()
        for i, d in enumerate(events, start=1):
            key = (d["tenant_id"], d["product_id"])
            self.assertNotIn(key, deleted_keys)
            self.assertEqual(d.keys(), previous[key].keys())
            self.assertEqual(d["version"], previous[key]["version"] + 1)
            self.assertGreaterEqual(d["stock"], 0)
            self.assertEqual(d["changed_at_ms"], 1000 * i)
            if d["deleted"]:
                deleted_keys.add(key)
                self.assertEqual(d["stock"], 0)
            changed_keys.add(key)
            previous[key] = d
        self.assertGreater(len(changed_keys), 10)
        self.assertEqual(sum(d["deleted"] for d in events[:600]), 4)
        self.assertEqual(len(deleted_keys), 67)

    def test_mutations_have_input_and_count_bounds(self):
        seed = source.seed_documents(sample_products())
        self.assertEqual(list(source.mutations(seed, count=0)), [])
        for count in (-1, True, 3.5, source.MAX_MUTATIONS + 1):
            with self.subTest(count=count), self.assertRaises(ValueError):
                list(source.mutations(seed, count))
        with self.assertRaisesRegex(ValueError, "unique"):
            list(source.mutations(seed[:-1]))

    def test_pinned_artifacts_rechecked_on_each_cache_read(self):
        payloads = {"LICENSE": b"test-license", "data/products.json": json.dumps(sample_products()).encode()}
        pins = {p: (len(data), hashlib.sha256(data).hexdigest()) for p, data in payloads.items()}
        def fetch(url, timeout):
            self.assertEqual(timeout, 15)
            path = url.split(source.REVISION + "/")[1]
            return io.BytesIO(payloads[path])
        with tempfile.TemporaryDirectory() as root, patch.object(source, "ARTIFACTS", pins):
            directory = Path(root)
            with patch.object(source, "urlopen", side_effect=fetch) as network:
                self.assertEqual(source.load_products(directory), sample_products())
                self.assertEqual(network.call_count, 2)
            with patch.object(source, "urlopen", side_effect=AssertionError("unexpected network")):
                self.assertEqual(source.load_products(directory), sample_products())
                (directory / source.REVISION / "LICENSE").write_bytes(b"same-length!")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    source.load_products(directory)

    def test_corrupt_network_response_never_replaces_cache(self):
        with tempfile.TemporaryDirectory() as root, patch.object(source, "urlopen", return_value=io.BytesIO(b"x")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                source.load_products(Path(root))
            self.assertFalse((Path(root) / source.REVISION / "LICENSE").exists())
