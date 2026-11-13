"""Cosmos extension v4 checkpoints after the batch callback finishes successfully."""
import json
import logging
import os
from contextlib import closing
from functools import lru_cache

import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

from contract import FIELDS, digest, encoded, payload
from database import connect

app = func.FunctionApp()


@lru_cache(maxsize=1)
def quarantine_container():
    client = BlobServiceClient(os.environ["QUARANTINE_ENDPOINT"], credential=ManagedIdentityCredential())
    return client.get_container_client("quarantine")


def quarantine(raw, reason):
    key = digest({"document": raw, "reason": reason})
    try:
        quarantine_container().upload_blob(
            name=f"rejected/{key}.json", data=encoded({"document": raw, "reason": reason}), overwrite=False
        )
    except ResourceExistsError:
        pass
    logging.warning("inventory_quarantined reason=%s evidence=%s", reason, key)


@app.function_name(name="InventoryProjection")
@app.retry(strategy="fixed_delay", max_retry_count="-1", delay_interval="00:00:10")
@app.cosmos_db_trigger(
    arg_name="documents", connection="InventoryCosmos", database_name="inventory",
    container_name="products", lease_container_name="leases",
    lease_container_prefix="inventory-sql-v1", create_lease_container_if_not_exists=False,
    start_from_beginning=True, max_items_per_invocation=100,
)
def project(documents: func.DocumentList):
    with closing(connect()) as db, closing(db.cursor()) as cursor:
        for item in documents:
            raw = json.loads(item.to_json())
            try:
                doc = payload(raw)
                source_ts = raw.get("_ts")
                if type(source_ts) is not int or not 0 <= source_ts <= 253402300799:
                    raise ValueError("invalid_source_epoch_s")
            except ValueError as error:
                quarantine(raw, str(error))
                continue
            # Cosmos _ts is the service's last mutation time in whole UTC seconds.
            values = [doc[field] for field in FIELDS] + [digest(doc), source_ts]
            row = cursor.execute("EXEC dbo.ApplyInventory ?,?,?,?,?,?,?,?,?,?", *values).fetchone()
            if row[0] == "conflict":
                quarantine(raw, "different_payload_at_same_version")
            else:
                logging.info("inventory_projection outcome=%s", row[0])
