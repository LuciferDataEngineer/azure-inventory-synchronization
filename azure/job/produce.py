"""Apply a bounded ordered JSONL workload with Cosmos point-reads and ETag preconditions."""
import argparse
import json
import logging
import os
import time

from azure.core import MatchConditions
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.identity import ManagedIdentityCredential

from contract import payload


def write_image(container, doc):
    key = doc["product_id"]
    body = dict(doc, id=key)
    for _ in range(8):
        try:
            try:
                current = container.read_item(item=key, partition_key=doc["tenant_id"])
            except CosmosResourceNotFoundError:
                container.create_item(body=body)
                return True
            if current["version"] == doc["version"] and payload(current) != doc:
                raise ValueError("source_version_conflict")
            if current["version"] >= doc["version"]:
                return False
            container.replace_item(item=key, body=body, etag=current["_etag"], match_condition=MatchConditions.IfNotModified)
            return True
        except CosmosHttpResponseError as error:
            if error.status_code not in (409, 412):
                raise
    raise RuntimeError("Concurrent writes exhausted retry budget")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", default="/app/workload.jsonl")
    parser.add_argument("--delay-ms", type=int, default=int(os.environ.get("MUTATION_DELAY_MS", "0")),
                        help="Minimum spacing after an applied version above 1 (0 to 500 ms)")
    opts = parser.parse_args()
    if not 0 <= opts.delay_ms <= 500:
        parser.error("--delay-ms must be between 0 and 500")
    with CosmosClient(os.environ["COSMOS_ENDPOINT"], ManagedIdentityCredential()) as client:
        products = client.get_database_client("inventory").get_container_client("products")
        writes = seen = 0
        with open(opts.file, encoding="utf-8") as stream:
            for line in stream:
                if seen >= 11000:
                    raise ValueError("11000-line lab workload cap")
                doc = payload(json.loads(line))
                applied = write_image(products, doc)
                writes += int(applied)
                seen += 1
                if applied and doc["version"] > 1 and opts.delay_ms:
                    time.sleep(opts.delay_ms / 1000)
    logging.warning("inventory_workload rows=%s writes=%s unchanged=%s", seen, writes, seen-writes)


if __name__ == "__main__":
    main()
