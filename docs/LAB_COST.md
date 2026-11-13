# Proposed Australia East lab budget

**Status on 14 November 2026.** Local runs used no billable Azure resources. The following is a pre-deployment estimate in AUD for a possible lab. Prices and service availability must be checked again with the actual subscription. The user has not approved this deployment or spending.

Allow four hours from group creation to completed teardown. Use one Linux B1 App Service plan shared by the Function and web dashboard, one Basic SQL database, one serverless Cosmos account, one manual Container Apps consumption job and two Standard LRS storage accounts. Use the same 1,000-product/10,000-mutation workload, one ten-minute consumer stop and one restart. Retain only aggregate results after the lab.

| Meter and planning assumption | AUD estimate |
|---|---:|
| App Service Linux B1, 4 hours × 0.027002 | 0.108 |
| SQL Database Single Basic, allowance of one full day × 0.251688 | 0.252 |
| Cosmos serverless, allowance of 1 million RUs × 0.405031 | 0.405 |
| Cosmos storage, allowance of 1 GB for a full month × 0.408584 | 0.409 |
| Container Apps job, 3 hours maximum with 0.25 vCPU × 0.000048 per second and 0.5 GiB × 0.000006 per GiB-second | 0.162 |
| Blob, allowance for 1 GB-month, 10,000 writes and 10,000 reads combined across both accounts | 0.113 |
| Log Analytics, 1 GB paid ingestion × 4.746678 | 4.747 |
| **Modeled subtotal** | **6.20** |

A proposed **AUD 10 planning allowance** leaves room above the modeled subtotal. The estimate excludes tax, registry hosting, variable egress and any resources retained after teardown. A usage spike or incomplete teardown can exceed the allowance. Review any charges from region-specific platform meters, the registered subscription offer and minimum billing units in the pricing calculator before seeking deployment approval. No free grant, discount or free trial is assumed. [Azure pricing calculator](https://azure.microsoft.com/en-au/pricing/calculator/)

Prices came from the Microsoft retail prices API with currency AUD, ARM region `australiaeast` and Consumption pricing. Representative meter IDs are `2319f407-1d8b-4f30-ab77-7120176287ab` (Linux B1), `14efde27-264a-44d2-a15c-8bd2cdb73481` (SQL Basic), `e8e551d0-eb3e-5ec5-a824-5a8554548311` (Cosmos serverless requests), `013772f8-63db-52a2-9c69-f2ae9012ef09` (Container Apps active vCPU), and `0d435df8-9c75-482e-a894-cd65c2160c7a` (Log Analytics). The Cosmos storage allowance uses the regional data-stored rate as a proxy to recheck. [Microsoft retail prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)

Before starting, assign a teardown owner, agree on the completion deadline and obtain explicit approval for the region, tiers, workload and AUD budget. During the lab record actual RU charges, job duration, telemetry volume and resource lifetime. At the agreed deadline stop the job, remove the dedicated group and temporary access, confirm the group is absent, then check Cost Management after usage posts. [Deployment controls and teardown commands](AZURE.md)
