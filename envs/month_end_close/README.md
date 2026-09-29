# Month-end close environment

A seeded Odoo world for grading agents on a long-horizon accounting task. It contains:

- **Summit Office Supply Co.**: 60 customers, 200 products, ~160 sales orders delivered and invoiced July to
  September 2026, with most older invoices paid.
- **Five planted receivables defects**, each a one-line story with named customers (misfiled payment, duplicate
  customer, overbilled invoice, unidentified wire, disputed short payment). They're hidden among ordinary invoices.
- **An evidence set**, written at seed time outside the repo, that records each defect and a baseline of every
  untouched invoice.
- **A verifier** that grades the final business state, not the agent's steps, and renders a PASS/FAIL scorecard.

| File | Purpose |
| --- | --- |
| `addons/month_end_close_env/populate/world.xml` | `populate` blueprint: customers, products, then the builder |
| `addons/month_end_close_env/models/res_company.py` | Deterministic builder: order-to-cash history and planted defects |
| `run.sh` | Drop, install and populate a database with a fixed seed |
| `TASK.md` | The prompt given to the agent |
| `verify.py` | Grader: scorecard, answer key and world dump (standard library only) |

## Build

Requires Python 3.12+, PostgreSQL 16 and an Odoo config whose `addons_path` includes `envs/month_end_close/addons`.

```bash
ODOO_CONF=~/odoo-env/odoo.conf ODOO_PYTHON=~/odoo-venv/bin/python envs/month_end_close/run.sh mec1 20260930
```

Seed and period are fixed (`--seed 20260930`, `-j 1`, all dates in July to September 2026), so two builds with the
same seed produce the same world. Check it with `verify.py --dump` on both and `diff`.

## Grade

```bash
envs/month_end_close/verify.py --db mec1 --html scorecard.html      # exit 0 only if every check passes
envs/month_end_close/verify.py --db mec1 --answer-key answer-key.html
```

Checks look at business facts (invoice balances, which customer a payment belongs to, net billed versus the order,
notes logged since seeding) rather than record IDs, so any valid path through the UI passes. A collateral check fails
the run if any invoice that wasn't planted changed.

## Keeping the solver cold

The evidence set lives in `$MONTH_END_EVIDENCE_DIR` (default `~/.local/share/month_end_close/`), not in the database
or the repo. Give the agent only `TASK.md` and the Odoo URL. This directory, which contains the builder, should not be
visible to the solving session.
