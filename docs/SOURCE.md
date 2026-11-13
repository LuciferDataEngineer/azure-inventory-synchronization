# Sample catalog and synthetic changes

The Azure Cosmos DB Conf 2026 team published a multitenant retail catalog for a querying and indexing demonstration. Its 100 sample products give the synchronization exercise realistic tenant boundaries, product keys and stock values. All business records in this project represent a demonstration catalog. [Upstream repository at the pinned revision](https://github.com/AzureCosmosDB/CosmosConf26QueryDemos/tree/e3611cb77542f6c77a90295054c3249db774d472)

## Provenance and reuse

Accessed 14 November 2026 (Australia/Melbourne). Both files resolve to commit `e3611cb77542f6c77a90295054c3249db774d472`. The repository carries the MIT license, copyright (c) 2026 Azure Cosmos DB. This project includes the upstream copyright and permission notice. [License copy](../third_party/CosmosConf26QueryDemos-LICENSE.txt)

| Upstream file | Download bytes | SHA-256 |
| --- | ---: | --- |
| [data/products.json](https://raw.githubusercontent.com/AzureCosmosDB/CosmosConf26QueryDemos/e3611cb77542f6c77a90295054c3249db774d472/data/products.json) | 1,575,138 | `eb7248f1afae33e258a15751ccb8e10b8629539f9e7f8411bea6e926e5b7edc1` |
| [LICENSE](https://raw.githubusercontent.com/AzureCosmosDB/CosmosConf26QueryDemos/e3611cb77542f6c77a90295054c3249db774d472/LICENSE) | 1,072 | `a278cd4b1e87ac004c68d7623d8bab1969595502adb5c82ef8351f60d22a9a45` |

The adapter verifies both the size and SHA-256 digest before installing each raw file in a revision-specific cache. Each download reads at most the expected size plus one byte and uses a 15-second network timeout. A cached run rechecks both files and works offline. To repair a corrupted cache, remove the affected file inside the revision directory and rerun. A different upstream revision requires a deliberate review of its license, schema and checksums.

## Document dictionary

The grain is one product in one tenant. `(tenant_id, product_id)` identifies the document and its SQL projection.

| Field | Definition |
| --- | --- |
| `tenant_id` | Source `tenantId`. All variants inherit their parent's tenant. |
| `product_id` | Upstream `id` for the 100 normalized examples. Generated variants append `-v1` through `-v9`. |
| `sku` | Source SKU with the same suffix for each synthetic variant. |
| `parent_id` | Original sample product ID. Together with the tenant it provides lineage. |
| `stock` | Nonnegative integer units. Original samples use upstream `inventory`; variant number 1–9 adds that many units to the parent's starting stock. Tombstones carry zero units. |
| `version` | Synthetic per-identity integer starting at one. Every mutation increments its identity's previous version. |
| `deleted` | Synthetic boolean soft-delete flag. Deleted items remain in the source and receive no further writes during the exercise. |
| `changed_at_ms` | Milliseconds since the synthetic workload epoch. Seeds start at zero. Mutation number *n* has timestamp *n* × 1,000. These are virtual times, suitable for the modeled outage timeline. |

The seed contains the 100 normalized sample identities and 900 generated identities. Each mutation yields a full document image. A deterministic stride selects from the active keys; every 149th write creates a retained tombstone while at least 11 keys are still active. This mixes different tenants, repeated updates to a single key and deletions in a known order. The generator publishes no real-world stock or demand estimates. Latest-version Cosmos change feed may coalesce writes to the same item. Consumers use versions and soft tombstones to converge on the current document image. [Microsoft change-feed modes](https://learn.microsoft.com/en-us/azure/cosmos-db/change-feed-modes)

## Replay the source check

From the repository root, using Python 3.10 or newer:

```bash
python3 -m unittest discover -s tests -p test_source.py
python3 - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from inventory_source import load_products, seed_documents, mutations

with TemporaryDirectory() as cache:
    source = load_products(Path(cache))
    seed = seed_documents(source)
    changes = list(mutations(seed, count=10000))
    print("source, seed, changes:", len(source), len(seed), len(changes))
    print("changed keys:", len({(d["tenant_id"], d["product_id"]) for d in changes}))
    print("tombstones:", sum(d["deleted"] for d in changes))
PY
```

Observed on 14 November 2026. The six isolated source tests passed. A fresh public-source download verified both hashes and produced 100 source rows across three tenants, 1,000 seed rows and 10,000 mutations across all 1,000 identities. Of those changes, 67 created retained tombstones, including four in the first 600 virtual seconds. The source check runs entirely on the local machine.
