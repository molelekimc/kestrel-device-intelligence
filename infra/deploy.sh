#!/usr/bin/env bash
# =============================================================================
# Ample Roots Digital | Kestrel Assembly Group (fictional) - Azure environment
#
# Creates, in your own Azure subscription:
#   1. A monthly budget with email alerts (subscription level)
#   2. Resource group           rg-ard-kestrel-dev
#   3. Data Lake (ADLS Gen2)    containers: bronze, silver, gold
#   4. Your data access         Storage Blob Data Contributor on the lake
#   5. Databricks workspace     Premium tier (needed for Unity Catalog)
#   6. Access Connector         Databricks' managed identity, also granted
#                               Storage Blob Data Contributor on the lake
#   7. Uploads ../raw into bronze/kestrel (optional, see config.env)
#
# Safe to re-run: existing resources are left as they are.
# Prerequisites: Azure CLI (brew install azure-cli), then `az login`.
# Costs: storage is cents; Databricks charges for compute only while a
# cluster runs. Use auto-terminating clusters. Run teardown.sh to remove all.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.env"

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# --- 0. Who and where -------------------------------------------------------
step "Checking your Azure sign-in"
az account show >/dev/null 2>&1 || { echo "Not signed in. Run: az login"; exit 1; }
SUB_ID=$(az account show --query id -o tsv)
SUB_NAME=$(az account show --query name -o tsv)
ME_ID=$(az ad signed-in-user show --query id -o tsv)
ME_UPN=$(az account show --query user.name -o tsv)
ALERT_EMAIL="${ALERT_EMAIL:-$ME_UPN}"
note "Subscription: $SUB_NAME ($SUB_ID)"
note "Signed in as: $ME_UPN"

# Globally unique names need a suffix; derive it from the subscription so
# re-runs always produce the same names.
SUFFIX=$(printf '%s' "$SUB_ID" | shasum | cut -c1-5)
STORAGE="st${PROJECT//-/}${SUFFIX}"            # e.g. stardkestrel1a2b3 (<=24 chars)
DBX_WS="dbw-${PROJECT}-${ENVIRONMENT}"
DBX_CONNECTOR="dbac-${PROJECT}-${ENVIRONMENT}"
TAGS=(project="$PROJECT" environment="$ENVIRONMENT" client="Kestrel Assembly Group (fictional)" owner="$ME_UPN")

step "Registering resource providers (first run on a new subscription takes a few minutes)"
for ns in Microsoft.Storage Microsoft.Databricks Microsoft.Consumption; do
  az provider register --namespace "$ns" --wait >/dev/null
  note "$ns registered"
done
az extension add --name databricks --upgrade --only-show-errors >/dev/null

# --- 1. Budget --------------------------------------------------------------
step "Creating monthly budget ($BUDGET_AMOUNT, alerts to $ALERT_EMAIL)"
START=$(date -u +%Y-%m-01T00:00:00Z)
az rest --method put \
  --url "https://management.azure.com/subscriptions/$SUB_ID/providers/Microsoft.Consumption/budgets/budget-${PROJECT}?api-version=2023-05-01" \
  --body "{
    \"properties\": {
      \"category\": \"Cost\", \"amount\": $BUDGET_AMOUNT, \"timeGrain\": \"Monthly\",
      \"timePeriod\": { \"startDate\": \"$START\" },
      \"notifications\": {
        \"actual80\":    { \"enabled\": true, \"operator\": \"GreaterThan\", \"threshold\": 80,
                          \"thresholdType\": \"Actual\",     \"contactEmails\": [\"$ALERT_EMAIL\"] },
        \"forecast100\": { \"enabled\": true, \"operator\": \"GreaterThan\", \"threshold\": 100,
                          \"thresholdType\": \"Forecasted\", \"contactEmails\": [\"$ALERT_EMAIL\"] }
      }
    }
  }" >/dev/null
note "Budget budget-${PROJECT} set"

# --- 2. Resource group ------------------------------------------------------
step "Creating resource group $RESOURCE_GROUP in $LOCATION"
az group create -n "$RESOURCE_GROUP" -l "$LOCATION" --tags "${TAGS[@]}" -o none

# --- 3. Data Lake -----------------------------------------------------------
step "Creating Data Lake storage account $STORAGE"
if ! az storage account show -n "$STORAGE" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az storage account create -n "$STORAGE" -g "$RESOURCE_GROUP" -l "$LOCATION" \
    --sku Standard_LRS --kind StorageV2 --hns true \
    --min-tls-version TLS1_2 --allow-blob-public-access false \
    --tags "${TAGS[@]}" -o none
fi
STORAGE_ID=$(az storage account show -n "$STORAGE" -g "$RESOURCE_GROUP" --query id -o tsv)

# --- 4. Your access to the data --------------------------------------------
step "Granting you Storage Blob Data Contributor on the lake"
note "(Owner lets you manage the account; this role lets you read and write the data in it.)"
az role assignment create --assignee-object-id "$ME_ID" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" -o none 2>/dev/null \
  || note "Already assigned"

step "Creating containers bronze, silver, gold"
note "New role assignments can take a few minutes to apply; retrying until they do."
for c in bronze silver gold; do
  for attempt in $(seq 1 20); do
    if az storage fs create -n "$c" --account-name "$STORAGE" --auth-mode login -o none 2>/dev/null \
       || az storage fs show -n "$c" --account-name "$STORAGE" --auth-mode login -o none 2>/dev/null; then
      note "$c ready"; break
    fi
    [ "$attempt" -eq 20 ] && { echo "Could not create $c. Wait a few minutes and re-run."; exit 1; }
    sleep 15
  done
done

# --- 5 & 6. Databricks ------------------------------------------------------
step "Creating Databricks Access Connector $DBX_CONNECTOR"
if ! az databricks access-connector show -n "$DBX_CONNECTOR" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az databricks access-connector create -n "$DBX_CONNECTOR" -g "$RESOURCE_GROUP" -l "$LOCATION" \
    --identity-type SystemAssigned --tags "${TAGS[@]}" -o none
fi
CONNECTOR_ID=$(az databricks access-connector show -n "$DBX_CONNECTOR" -g "$RESOURCE_GROUP" --query id -o tsv)
CONNECTOR_PRINCIPAL=$(az databricks access-connector show -n "$DBX_CONNECTOR" -g "$RESOURCE_GROUP" --query identity.principalId -o tsv)

step "Granting the Access Connector Storage Blob Data Contributor on the lake"
az role assignment create --assignee-object-id "$CONNECTOR_PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" -o none 2>/dev/null \
  || note "Already assigned"

step "Creating Databricks workspace $DBX_WS (Premium; takes 3-5 minutes)"
if ! az databricks workspace show -n "$DBX_WS" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az databricks workspace create -n "$DBX_WS" -g "$RESOURCE_GROUP" -l "$LOCATION" \
    --sku premium --managed-resource-group "${RESOURCE_GROUP}-dbx-managed" \
    --tags "${TAGS[@]}" -o none
fi
DBX_URL=$(az databricks workspace show -n "$DBX_WS" -g "$RESOURCE_GROUP" --query workspaceUrl -o tsv)

# --- 7. Land the raw data ---------------------------------------------------
if [ "$UPLOAD_RAW_DATA" = "true" ] && [ -d "$HERE/../raw" ]; then
  step "Uploading ../raw to bronze/kestrel"
  for src in intune telemetry plant_it; do
    az storage fs directory upload -f bronze --account-name "$STORAGE" --auth-mode login \
      -s "$HERE/../raw/$src" -d kestrel --recursive -o none
    note "bronze/kestrel/$src uploaded"
  done
fi

# --- Summary ----------------------------------------------------------------
step "Done"
cat <<EOF
    Resource group     $RESOURCE_GROUP ($LOCATION)
    Data Lake          $STORAGE
                       abfss://bronze@${STORAGE}.dfs.core.windows.net/kestrel/
    Databricks         https://$DBX_URL
    Access Connector   $CONNECTOR_ID
    Budget             $BUDGET_AMOUNT/month, alerts to $ALERT_EMAIL

    Next, in Databricks: create a storage credential from the Access Connector ID
    above, then external locations for the bronze, silver and gold containers.
EOF
