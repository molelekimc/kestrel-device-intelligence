#!/usr/bin/env bash
# Deletes the whole Kestrel environment created by deploy.sh: the resource
# group, everything in it, and the Databricks managed resource group.
# The data in the lake is deleted too. The budget is kept (it costs nothing).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.env"

az account show >/dev/null 2>&1 || { echo "Not signed in. Run: az login"; exit 1; }
echo "This permanently deletes resource group '$RESOURCE_GROUP' and all data in it."
read -r -p "Type the resource group name to confirm: " answer
[ "$answer" = "$RESOURCE_GROUP" ] || { echo "Cancelled."; exit 1; }

az group delete -n "$RESOURCE_GROUP" --yes
echo "Deleted. Re-create any time with ./deploy.sh (then re-upload the raw data)."
