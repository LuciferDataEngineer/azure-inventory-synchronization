"""Pin the Cosmos Conf sample and generate repeatable full inventory document images."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

UPSTREAM = "https://raw.githubusercontent.com/AzureCosmosDB/CosmosConf26QueryDemos"
REVISION = "e3611cb77542f6c77a90295054c3249db774d472"
ARTIFACTS = {
    "LICENSE": (1072, "a278cd4b1e87ac004c68d7623d8bab1969595502adb5c82ef8351f60d22a9a45"),
    "data/products.json": (
        1575138, "eb7248f1afae33e258a15751ccb8e10b8629539f9e7f8411bea6e926e5b7edc1"
    ),
}
MAX_MUTATIONS = 1_000_000


def _read_artifact(directory, relative_path):
    size, expected_digest = ARTIFACTS[relative_path]
    target = directory / relative_path
    try:
        stream = target.open("rb")
    except FileNotFoundError:
        stream = urlopen(f"{UPSTREAM}/{REVISION}/{relative_path}", timeout=15)
        cached = False
    else:
        cached = True

    with stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError(f"Size or SHA-256 mismatch for {relative_path}. Remove a damaged cache file and retry.")

    if not cached:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Readers always see a complete, verified artifact, even during a concurrent fetch.
        pending = NamedTemporaryFile(dir=target.parent, delete=False)
        temporary = Path(pending.name)
        try:
            with pending:
                pending.write(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return data


def load_products(data_dir: Path) -> list[dict]:
    """Verify the license and payload on every read. Keep raw bytes under the pinned SHA."""
    directory = Path(data_dir) / REVISION
    _read_artifact(directory, "LICENSE")
    products = json.loads(_read_artifact(directory, "data/products.json"))
    _validate_products(products)
    return products


def _validate_products(products):
    if not isinstance(products, list) or len(products) != 100:
        raise ValueError("Expected the 100-product Cosmos Conf sample")
    keys = set()
    for row in products:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(k), str) or not row[k].strip() for k in ("tenantId", "id", "sku")
        ):
            raise ValueError("Missing source identity")
        if type(row.get("inventory")) is not int or row["inventory"] < 0:
            raise ValueError("Inventory must be a nonnegative integer")
        key = (row["tenantId"], row["id"])
        if key in keys:
            raise ValueError("Duplicate source product key")
        keys.add(key)


def seed_documents(products):
    """Return 100 normalized upstream examples and 900 synthetic variants."""
    _validate_products(products)
    docs = []
    for variant in range(10):
        for product in sorted(products, key=lambda p: (p["tenantId"], p["id"])):
            suffix = f"-v{variant}" if variant else ""
            docs.append({
                "tenant_id": product["tenantId"],
                "product_id": product["id"] + suffix,
                "sku": product["sku"] + suffix,
                "stock": product["inventory"] + variant,
                "version": 1,
                "deleted": False,
                "changed_at_ms": 0,
                "parent_id": product["id"],
            })
    if len({(row["tenant_id"], row["product_id"]) for row in docs}) != len(docs):
        raise ValueError("Variant identity collides with a seed product")
    return docs


def mutations(seed, count=10000):
    """Yield synthetic post-write images in arrival order, one per virtual second."""
    if type(count) is not int or not 0 <= count <= MAX_MUTATIONS:
        raise ValueError(f"Mutation count must be an integer between 0 and {MAX_MUTATIONS}")
    active = [dict(row) for row in sorted(seed, key=lambda r: (r["tenant_id"], r["product_id"]))]
    if len(active) != 1000 or len({(r["tenant_id"], r["product_id"]) for r in active}) != 1000:
        raise ValueError("Expected 1,000 unique seeded product identities")
    if any(row["version"] != 1 or row["changed_at_ms"] != 0 or row["deleted"] for row in active):
        raise ValueError("Expected the initial version-one seed")

    for ordinal in range(1, count + 1):
        index = (ordinal * 137) % len(active)
        previous = active[index]
        deleted = ordinal % 149 == 0 and len(active) > 10
        delta = -(ordinal % 5 + 1) if ordinal % 2 == 0 else ordinal % 7 + 1
        updated = {
            **previous,
            "version": previous["version"] + 1,
            "changed_at_ms": ordinal * 1000,
            "stock": 0 if deleted else max(0, previous["stock"] + delta),
            "deleted": deleted,
        }
        if deleted:
            # Tombstones remain visible in the source. Exclude them from future writes.
            active[index] = active[-1]
            active.pop()
        else:
            active[index] = updated
        yield dict(updated)
