"""Run explicitly with the three Azure dependency sets. No service endpoints are contacted."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, create_autospec, patch

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import HttpTransport, RequestsTransportResponse
from azure.cosmos import ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.functions import Document, DocumentList
from azure.storage.blob import ContainerClient
import requests

AZURE_DIR = Path(__file__).resolve().parents[1] / "azure"
for directory in ("shared", "functions", "job", "web"):
    sys.path.insert(0, str(AZURE_DIR / directory))

import app as web
import contract
import function_app
import produce

DOC = {"tenant_id": "tenant", "product_id": "one", "sku": "sku", "parent_id": "one",
       "stock": 3, "version": 2, "deleted": False, "changed_at_ms": 1000}


class RecordingTransport(HttpTransport):
    """Record serialized requests at the Blob SDK's HTTP boundary."""
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        status = next(self.statuses)
        response = requests.Response()
        response.url = request.url
        response.status_code = status
        response.reason = "Created" if status == 201 else "Lab error"
        response._content = b""
        if status != 201:
            code = "BlobAlreadyExists" if status == 409 else "AuthorizationFailure"
            response.headers["x-ms-error-code"] = code
            response.headers["Content-Type"] = "application/xml"
            response._content = f"<Error><Code>{code}</Code><Message>Lab error</Message></Error>".encode()
        return RequestsTransportResponse(request, response)


def blob_client(*statuses):
    transport = RecordingTransport(statuses)
    return ContainerClient("https://example.invalid", "quarantine", transport=transport, retry_total=0), transport


def batch(*documents):
    return DocumentList([Document(d) for d in documents])


class FunctionContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        [cls.indexed] = function_app.app.get_functions()

    def test_indexed_trigger_and_retry_settings(self):
        self.assertEqual(self.indexed.get_function_name(), "InventoryProjection")
        [binding] = json.loads(self.indexed.get_function_json())["bindings"]
        self.assertEqual(binding, {
            "name": "documents", "direction": "IN", "type": "cosmosDBTrigger",
            "connection": "InventoryCosmos", "databaseName": "inventory", "containerName": "products",
            "leaseContainerName": "leases", "leaseContainerPrefix": "inventory-sql-v1",
            "maxItemsPerInvocation": 100, "startFromBeginning": True, "createLeaseContainerIfNotExists": False,
        })
        policy = self.indexed.get_settings_dict("retry_policy")
        self.assertEqual((policy["strategy"], policy["max_retry_count"], policy["delay_interval"]),
                         ("fixed_delay", "-1", "00:00:10"))

    def test_actual_document_batch_binds_sql_parameters(self):
        db = Mock()
        cursor = db.cursor.return_value
        cursor.execute.return_value.fetchone.return_value = ("applied",)
        doc = dict(DOC, id="one", _ts=1234, _etag="etag")
        with patch.object(function_app, "connect", return_value=db):
            self.indexed(batch(doc))
        statement, *values = cursor.execute.call_args.args
        self.assertIn("ApplyInventory", statement)
        # Positional order in dbo.ApplyInventory. Keep this independent of the adapter's FIELDS list.
        self.assertEqual(values, ["tenant", "one", "sku", "one", 3, 2, False, 1000, contract.digest(DOC), 1234])
        db.close.assert_called_once()
        cursor.close.assert_called_once()

    def test_quarantine_replays_as_conditional_blob_upload(self):
        client, transport = blob_client(201, 409)
        raw = {"sku": "broken", "_ts": 1}
        db = Mock()
        with patch.object(function_app, "connect", return_value=db), patch.object(
            function_app, "quarantine_container", return_value=client
        ):
            self.indexed(batch(raw))
            self.indexed(batch(raw))
        self.assertEqual(len(transport.requests), 2)
        first, replay = transport.requests
        self.assertEqual((first.method, first.url, first.body), (replay.method, replay.url, replay.body))
        self.assertEqual(first.method, "PUT")
        self.assertEqual(first.headers["If-None-Match"], "*")
        body = json.loads(first.body)
        self.assertEqual(body["document"], raw)
        self.assertTrue(body["reason"])
        self.assertTrue(first.url.endswith("/rejected/" + contract.digest(body) + ".json"))
        db.cursor.return_value.execute.assert_not_called()

    def test_sql_conflict_quarantined_and_upload_failure_propagates(self):
        client, transport = blob_client(403)
        db = Mock()
        db.cursor.return_value.execute.return_value.fetchone.return_value = ("conflict",)
        raw = dict(DOC, id="one", _ts=1234)
        with patch.object(function_app, "connect", return_value=db), patch.object(
            function_app, "quarantine_container", return_value=client
        ), self.assertRaises(HttpResponseError) as error:
            self.indexed(batch(raw))
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(json.loads(transport.requests[0].body)["reason"], "different_payload_at_same_version")
        db.close.assert_called_once()

    def test_invalid_source_time_quarantines_then_continues_batch(self):
        bad = [dict(DOC), dict(DOC, _ts=True), dict(DOC, _ts=-1), dict(DOC, _ts=253402300800)]
        client, transport = blob_client(*(201 for _ in bad))
        db = Mock()
        cursor = db.cursor.return_value
        cursor.execute.return_value.fetchone.return_value = ("applied",)
        with patch.object(function_app, "connect", return_value=db), patch.object(
            function_app, "quarantine_container", return_value=client
        ):
            self.indexed(batch(*bad, dict(DOC, _ts=0)))
        rejects = [json.loads(req.body) for req in transport.requests]
        self.assertEqual(rejects, [{"document": raw, "reason": "invalid_source_epoch_s"} for raw in bad])
        cursor.execute.assert_called_once()

    def test_sql_failure_propagates_to_trigger_retry(self):
        db = Mock()
        db.cursor.return_value.execute.side_effect = RuntimeError("injected SQL outage")
        with patch.object(function_app, "connect", return_value=db), self.assertRaisesRegex(RuntimeError, "SQL outage"):
            self.indexed(batch(dict(DOC, _ts=2000)))
        db.close.assert_called_once()


class CosmosProducerContracts(unittest.TestCase):
    def setUp(self):
        self.container = create_autospec(ContainerProxy, instance=True)
        self.body = dict(DOC, id=DOC["product_id"])

    def test_create_conflict_rereads_competing_version(self):
        self.container.read_item.side_effect = [CosmosResourceNotFoundError(status_code=404), self.body]
        self.container.create_item.side_effect = CosmosHttpResponseError(status_code=409)
        self.assertFalse(produce.write_image(self.container, DOC))
        self.container.create_item.assert_called_once_with(body=self.body)
        self.assertEqual(self.container.read_item.call_count, 2)
        self.container.read_item.assert_called_with(item="one", partition_key="tenant")
        self.container.replace_item.assert_not_called()

    def test_conditional_replace_retries_with_new_etag(self):
        self.container.read_item.side_effect = [dict(self.body, version=1, _etag="a"),
                                                dict(self.body, version=1, _etag="b")]
        self.container.replace_item.side_effect = [CosmosHttpResponseError(status_code=412), {}]
        self.assertTrue(produce.write_image(self.container, DOC))
        for call, etag in zip(self.container.replace_item.call_args_list, ("a", "b"), strict=True):
            self.assertEqual(call.kwargs, {"item": "one", "body": self.body, "etag": etag,
                                          "match_condition": MatchConditions.IfNotModified})

    def test_identical_stale_and_conflicting_versions(self):
        self.container.read_item.side_effect = [self.body, dict(self.body, version=3), dict(self.body, stock=99)]
        self.assertFalse(produce.write_image(self.container, DOC))
        self.assertFalse(produce.write_image(self.container, DOC))
        with self.assertRaisesRegex(ValueError, "conflict"):
            produce.write_image(self.container, DOC)
        self.container.replace_item.assert_not_called()
        self.container.create_item.assert_not_called()

    def test_retry_budget_and_non_race_error(self):
        self.container.read_item.return_value = dict(self.body, version=1, _etag="a")
        self.container.replace_item.side_effect = CosmosHttpResponseError(status_code=412)
        with self.assertRaises(RuntimeError):
            produce.write_image(self.container, DOC)
        self.assertEqual(self.container.replace_item.call_count, 8)
        self.container.read_item.side_effect = CosmosHttpResponseError(status_code=403)
        with self.assertRaises(CosmosHttpResponseError) as error:
            produce.write_image(self.container, DOC)
        self.assertEqual(error.exception.status_code, 403)


class DashboardContracts(unittest.TestCase):
    def test_route_displays_escaped_sql_totals_and_handles_empty_state(self):
        db = Mock()
        cursor = db.cursor.return_value
        cursor.execute.return_value.fetchall.side_effect = [[("<tenant>", 5, 11, 2)], []]
        with patch.object(web, "connect", return_value=db), web.app.test_client() as client:
            result = client.get("/")
            self.assertEqual(result.status_code, 200)
            self.assertIn(b"&lt;tenant&gt;</th><td>5</td><td>11</td><td>2</td>", result.data)
            self.assertIn(b"No products projected", client.get("/").data)
            self.assertEqual(client.post("/").status_code, 405)
        self.assertEqual(db.close.call_count, 2)
        self.assertTrue(all("FROM dbo.InventoryTotals" in call.args[0] for call in cursor.execute.call_args_list))
