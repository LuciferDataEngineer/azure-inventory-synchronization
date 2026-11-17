# Offline Azure adapter checks

Run from the repository root with a fresh Python environment. Installing wheels uses the Python package index. The test run itself uses local fixtures and in-process doubles and requires no Azure account or credentials.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r azure/functions/requirements.txt -r azure/job/requirements.txt -r azure/web/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s integration -p test_adapters.py -v
```

The Functions SDK indexes the Cosmos trigger and exposes its lease container, prefix, batch size and retry policy. The tests call that indexed function with genuine SDK documents. They check the Azure SQL procedure's positional arguments, quarantine of missing or invalid source timestamps, continuation past rejected rows and propagation of SQL errors. The Blob SDK serializes quarantine requests through a recording HTTP transport, including the conditional-create header, evidence bytes, handling an existing blob and propagating a failed upload.

The producer tests use a mock constrained to the Cosmos SDK's container interface and real Cosmos SDK exceptions. They check the ETag replacement condition, retries after concurrent create and replace, version conflicts, replay and retry exhaustion. The Flask test client exercises populated and empty dashboard states and checks HTML escaping with an injected database cursor.

Observed on 14 November 2026 with Python 3.14.7 on macOS. All 11 adapter tests and the dependency consistency check passed. The deployment images specify Python 3.12. The current tests exercise application and SDK contracts locally. Function host checkpointing, SQL Server procedure execution, managed identity, deployed HTTP endpoints, cloud latency and service recovery require separate validation in Azure.

## Python runtime compatibility checked on 18 November 2026

The initial GitHub adapter job identified a Python-version mismatch in the Functions SDK pin. The Python 3.12 deployment image uses `azure-functions==1.26.0`. Python 3.13+ environments use `azure-functions==2.4.0`. Requirement markers select the compatible package, and the adapter job now runs on both Python 3.12 and 3.14. [Functions SDK 1.26.0 metadata](https://pypi.org/project/azure-functions/1.26.0/) · [Functions SDK 2.4.0 metadata](https://pypi.org/project/azure-functions/2.4.0/)

Fresh isolated environments on macOS passed all 11 adapter tests and dependency consistency checks on Python 3.12.16 and 3.14.7. All 25 core tests also passed on Python 3.12.16. Create the environment with `python3.12 -m venv .venv` to exercise the deployment image's Python version. These checks exercise SDK contracts with local transports and test doubles.
