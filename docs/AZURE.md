# Azure deployment target

**Deployment status: untested in Azure.** Local SQLite evidence and the offline compiler/SDK checks in this repository describe different environments. No cloud resources, subscription, credentials or spending are needed to run the local project.

## Components and access

| Component | Work it performs | Identity and access |
|---|---|---|
| Container Apps manual job | Reads an image-bundled JSONL workload of up to 11,000 records, creates new products and conditionally replaces newer revisions using the current Cosmos ETag. A restart can replay the same file. Conflicting equal revisions fail the producer. | Its system identity is a Cosmos data contributor on the products container only. |
| Cosmos DB serverless, single region | The inventory database holds products partitioned by tenant and a persistent lease container partitioned by ID. Writes retain soft-delete tombstones and no TTL is configured. | Key-based data access is disabled. Cosmos data access remains on public TLS endpoints protected by Entra RBAC. |
| Python Functions v4 | The Cosmos extension reads the latest-version feed from the beginning on a fresh lease namespace. It commits or quarantines each document before returning the batch. Unhandled operational errors trigger unbounded fixed-delay retries. Replay after a crash is safe because SQL and Blob effects are idempotent. | Read products, update leases, own blobs on an isolated Functions host store, write the separate quarantine container. SQL grants only EXECUTE on the projection procedure. |
| Azure SQL Database Basic, 5 DTU | A transaction takes an update/range lock on the binary-collated tenant and product key, applies only a greater version, and records the observed version. Conflicting repeats return a conflict result. Retained tombstones prevent stale resurrection. | Entra-only server authentication. Firewall access restricted to the delegated apps subnet and one explicitly approved operator IPv4. |
| Blob quarantine | Saves the rejected raw Cosmos document, including service metadata, and the validation reason. Its content-addressed path tolerates retries. A storage error propagates and stops acknowledgement. | Separate storage account with private containers, key access disabled. No anonymous downloads. |
| Linux App Service | A read-only Flask dashboard shows active product count, stock and tombstones per tenant. Functions and the dashboard share one always-on B1 dedicated plan in this lab target. | Separate system identity with SELECT on the aggregate view only. The operator IPv4 allowlist applies to the site and SCM endpoint. |
| Application Insights, Log Analytics | Function batch executions, failures and per-outcome traces; Container Apps console and system diagnostics. | No credentials in application source. Sampling of traces is enabled. SQL observations give a durable denominator independent of sampled logs. |

SKU, stock and versions are sample inputs. Keep data and image registries public only for this legally reusable workload. The managed service endpoints use public TLS; private endpoint isolation and Entra-authenticated multi-user dashboard access would require additional configuration and verification. The app allowlist authorizes a network address, so its holder must control that egress point.

The change feed can collapse intermediate versions. A physical delete may never be delivered. Keep deletes as versioned tombstones and preserve both the container and lease prefix when restarting the Function. Current-state convergence is the recovery criterion. [Microsoft change feed modes](https://learn.microsoft.com/en-us/azure/cosmos-db/change-feed-modes) · [Cosmos trigger settings](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-cosmosdb-v2-trigger) · [Function retry behavior](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-error-pages)

## Credential-free preparation

Run from the repository root. The workload export uses the pinned licensed source and can reuse a verified local cache. Set the Bicep binary path to your installation of v0.46.1.

```bash
python3 inventory_demo.py export --source-dir data/source --output data/workload.jsonl
python3 -m unittest discover -s tests -v
python3 infra/check_parameters.py --bicep "$HOME/.azure/bin/bicep"
```

With a local Docker engine, these commands build three image candidates without creating cloud resources. Azure App Service and the job target Linux AMD64. The job image includes the upstream MIT notice. Inspect the image, refresh supported base image versions and record the three image digests before a deployment. Image builds and cloud image startup require separate verification.

```bash
docker build --platform linux/amd64 -f azure/functions/Dockerfile -t inventory-projector:lab .
docker build --platform linux/amd64 -f azure/web/Dockerfile -t inventory-dashboard:lab .
docker build --platform linux/amd64 -f azure/job/Dockerfile -t inventory-mutations:lab .
```

## Local target checks on 14 November 2026

Bicep CLI 0.46.1 compiled the eight-parameter template and environment-backed development parameters with 26 ARM resource declarations and no template diagnostics. With the same synthetic settings, the production parameter file was refused (BCP033). SQLFluff 4.5.0 parsed all three Azure T-SQL files in the `tsql` dialect with no parse errors. The three dependency-free cloud contract tests passed. The DevOps YAML loaded locally. Offline checks rejected unapproved references, non-unicast operator IPs, mutable image tags, empty identities and mismatched target subscriptions, groups or regions. No SQL Server query, managed identity authorization, Azure DevOps deployment or image boot is implied by those syntax and contract checks.

To repeat the optional SQL parsing check in a disposable Python environment:

```bash
python -m pip install sqlfluff==4.5.0
for file in sql/azure_*.sql; do sqlfluff parse --dialect tsql "$file" >/dev/null || exit 1; done
```

## Gated lab deployment

The pipeline defaults to credential-free checks and has no push or PR cloud trigger. Live deployment requires an explicit region, a dedicated resource group in that region, service availability, workload plan, current estimated AUD budget, approval and cleanup owner. The pipeline does not create a billing account, maintain a spending cap, estimate a price or start the mutation job. The B1 plan, SQL and telemetry can accrue charges while idle.

1. Review service exact pricing for the selected region and the approved lab runtime. Include the Linux B1 plan, SQL Basic 5 DTU, Cosmos serverless request units and storage, Container Apps job compute, two LRS accounts and monitoring ingestion. The single-worker job stops after completion or three hours. This is a timeout for one job, not a limit on account spend. Record the managed lab end time, teardown owner and expected image-registry costs separately.
2. After approval, publish the three lab images to a registry permitting anonymous pulls by immutable digest. Configure an Azure DevOps federated service connection scoped to the dedicated group. The deploying identity needs resource creation and scoped role-assignment access. Configure an **external approval check** on the DevOps environment `inventory-lab-approved`. The YAML environment reference alone does not create that approval. Approval for a different project or region does not transfer.
3. Create the variable group `inventory-approved-lab` containing `LAB_SUBSCRIPTION_ID`, `LAB_LOCATION`, `LAB_GROUP`, `APPROVAL_REFERENCE`, `OPERATOR_IP`, `SQL_ADMIN_GROUP_ID`, `SQL_ADMIN_GROUP_NAME`, `FUNCTION_IMAGE`, `WEB_IMAGE`, `MUTATION_IMAGE`. Set the subscription GUID and canonical region explicitly, for example `australiaeast` only if it is the approved lab region. The helper reads the selected resource group’s ARM ID and metadata location and verifies both before preview and apply. This lab explicitly places its resources in the group’s location. Use public digest references, for example `registry.example/team/project@sha256:<64-hex>`. SQL privileges are granted separately through the approved administrator group. Queue the manual pipeline with `deploy=true` and the approved service connection. The helper always supplies `infra/dev.bicepparam`, which reads the variable-group settings at compilation. Infrastructure operators require management-plane rights on the lab group. The SQL administrator GUID and display name identify a separate Entra **group in the target subscription’s tenant**. The client operator must be a member of that group and connect through the specified public egress IP. Verify the ID/name/tenant pairing in Entra; the offline validator checks formats only. Review the ARM what-if and the budget before approving the environment. The apply stage deploys ARM infrastructure only. Azure DevOps must be configured externally.
4. Read the deployment outputs. Set the matching environment variables from that reviewed deployment. Using Microsoft sqlcmd authenticated as the Entra administrator from the approved IP, execute the schema and narrow grants. Runtime principal IDs are distinct from application client IDs. Restart the Function after data roles have propagated if needed.

   ```bash
   az deployment group show -g "$LAB_GROUP" -n inventory-lab --query properties.outputs -o json
   sqlcmd -G -S "$SQL_HOST" -d Inventory -b -i sql/azure_projection.sql
   sqlcmd -G -S "$SQL_HOST" -d Inventory -b -v FUNCTION_PRINCIPAL="$FUNCTION_PRINCIPAL" WEB_PRINCIPAL="$WEB_PRINCIPAL" -i sql/azure_permissions.sql
   ```

5. During the approved window only, run the workload and inspect execution logs, Function invocation errors and SQL observations. Open the App Service hostname returned by the deployment from the allowlisted network.

   ```bash
   az containerapp job start -g "$LAB_GROUP" -n "$JOB_NAME"
   az containerapp job execution list -g "$LAB_GROUP" -n "$JOB_NAME" -o table
   sqlcmd -G -S "$SQL_HOST" -d Inventory -b -i sql/azure_observation.sql
   ```

## Production parameter design

`infra/prod.bicepparam` is a negative test that inherits dev-shaped fields and requests the unsupported `prod` stage. The emitted ARM template accepts only `dev`. The pipeline has no production route. A future production implementation needs its own subscription and reviewed environment, explicit workload and capacity budgets, private networking and DNS, authenticated dashboard audience, image registry access, multi-instance sizing, monitoring and retention, disaster-recovery objectives, and independently validated failover and reconciliation. Those values belong to a separately approved production design.

## Cloud acceptance still required

* Compare the final Cosmos products to Azure SQL by tenant and ID, version, SKU, stock, parent ID, deleted flag and logical time. Report counts and payload mismatches, including retained tombstones. A dashboard row count alone cannot detect a stale SKU.
* The SQL readout approximates write lag for distinct applied versions from Cosmos service `_ts` (whole UTC seconds) to SQL transaction time. Do not use the synthetic `changed_at_ms` for live lag. The readout omits coalesced versions and does not establish client-visible p95. For the 60-second target, additionally timestamp each acknowledged Cosmos write with the observer’s monotonic clock, poll SQL from a separate connection, record matched version visibility, and report coalesced versions and censored observations separately.
* Conduct an actual ten-minute consumer outage with the source active and soft deletes occurring during the outage. The manual job waits 100 ms after each newly applied mutation above version one. For 10,000 such mutations, those waits alone provide a window of at least 1,000 seconds. Begin the outage after the seed has been applied. In a fresh isolated run, pause the Function for 600 wall-clock seconds while the job continues, capture the tombstone counts, and resume it. Keep the original lease container and prefix, then restart, replay duplicates and compare state to the source. Confirm the SQL transaction and quarantine retries, expired identity tokens, permissions, firewall restrictions, throughput and costs under actual Azure conditions.
* Invalid documents are content-addressed in quarantine, and equal-version conflicts leave the existing SQL value intact. Resolve the source issue and publish a strictly greater valid version. Monitor repeated failures, especially SQL/Blob connectivity, which hold a lease batch pending. If the target is lost, restore SQL or rebuild into an empty target with a new lease namespace and replay from the beginning; reconcile first, then switch readers.

## Cleanup

After capturing the approved validation evidence and any needed quarantine records, stop running job executions. Have the lab owner verify that the dedicated resource group contains only the lab resources, then delete it and confirm deletion. Remove external image tags/artifacts and release temporary identity assignments or DevOps authorizations separately. ARM deletion may retain backup or soft-deleted data per service policy.

```bash
az resource list -g "$LAB_GROUP" -o table
az group delete --name "$LAB_GROUP" --yes
az group exists --name "$LAB_GROUP"
```
