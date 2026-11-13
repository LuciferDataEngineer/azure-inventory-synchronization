"""Credential-free tests against the SQL boundary, using Cosmos-shaped inputs."""
import unittest

from azure.shared.contract import digest, payload


class CloudContractTests(unittest.TestCase):
    def setUp(self):
        self.doc = dict(tenant_id="west", product_id="demo-1", sku="TEST", parent_id="demo",
                        stock=5, version=1, deleted=False, changed_at_ms=0)

    def test_system_metadata_does_not_change_business_hash(self):
        raw = dict(self.doc, id="demo-1", _ts=1600000000, _etag="opaque")
        self.assertEqual(self.doc, payload(raw))
        self.assertEqual(digest(self.doc), digest(payload(raw)))
        self.assertNotEqual(digest(self.doc), digest(dict(self.doc, stock=6)))

    def test_key_integrity_and_sql_nvarchar_capacity(self):
        for fields in ({"id": "another"}, {"tenant_id": "tenant "}, {"product_id": "😀"*65}, {"parent_id": None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                payload(dict(self.doc, **fields))
        self.assertEqual("😀"*64, payload(dict(self.doc, sku="😀"*64))["sku"])

    def test_sql_numeric_and_tombstone_contract(self):
        for fields in ({"stock": True}, {"stock": -1}, {"version": 0}, {"deleted": 1},
                       {"changed_at_ms": 2**63}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                payload(dict(self.doc, **fields))
        self.assertTrue(payload(dict(self.doc, stock=0, deleted=True))["deleted"])
