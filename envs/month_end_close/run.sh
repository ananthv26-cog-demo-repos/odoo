#!/usr/bin/env bash
# Build a fresh month-end close environment.
#   usage: envs/month_end_close/run.sh <database> [seed]
# Requires an odoo config (ODOO_CONF, default ~/odoo-env/odoo.conf) whose addons_path includes
# envs/month_end_close/addons, and a Python with Odoo's requirements (ODOO_PYTHON, default python3).
set -euo pipefail

DB="${1:?usage: run.sh <database> [seed]}"
SEED="${2:-20260930}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CONF="${ODOO_CONF:-$HOME/odoo-env/odoo.conf}"
PY="${ODOO_PYTHON:-python3}"
ODOO=("$PY" "$REPO/odoo-bin")

"${ODOO[@]}" db -c "$CONF" drop "$DB" >/dev/null 2>&1 || true
"${ODOO[@]}" server -c "$CONF" -d "$DB" -i month_end_close_env --stop-after-init
"${ODOO[@]}" server -c "$CONF" -d "$DB" -u populate --stop-after-init
"${ODOO[@]}" populate -c "$CONF" -d "$DB" -b month_end_close_env.world --seed "$SEED" -j 1

echo "Environment ready in database '$DB' (seed $SEED)."
echo "Evidence set: ${MONTH_END_EVIDENCE_DIR:-$HOME/.local/share/month_end_close}/$DB-evidence.json"
