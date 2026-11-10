# Kestrel Assembly Group — Enterprise Device Intelligence

**Ample Roots Digital · Case study 01**

> Ample Roots Digital (ARD) is a fictional consultancy, and Kestrel Assembly Group is a fictional client. All data in this repository is synthetic. No employer data, code or business rules are used.

Kestrel runs about 18,000 Windows devices across three plants. Device data sits in three disconnected systems, so IT leadership can't say which plants or device groups are unstable, or whether changes like the Windows 11 25H2 rollout are helping.

## Data

```
raw/
  intune/      Intune device inventory export (CSV)
  telemetry/   Reliability agent events, one gzipped JSON-lines file per day (8 weeks)
  plant_it/    Plant IT's 25H2 rollout tracker spreadsheet (CSV)
```

See [SOURCES.md](SOURCES.md) for what each source contains and how they relate.

Regenerate the data:

```bash
pip install pandas numpy
python generate_sources.py
```

`SEED` and `N_DEVICES` at the top of the script control the run.

## Azure environment

`infra/` builds the cloud environment with the Azure CLI: a cost budget with alerts, a resource group, a Data Lake (bronze / silver / gold), a Databricks workspace, and least-privilege access through managed identities instead of keys.

```bash
az login
cd infra
./deploy.sh      # create everything (safe to re-run)
./teardown.sh    # delete everything when not in use
```

Settings live in `infra/config.env`.

## Pipeline (in progress)

Bronze (raw, as landed) → Silver (cleaned, typed, one device key) → Gold (KPIs by plant, class, model, build and week) → C# API → Angular portal.
