"""Load a pack's CRM export and add the derived fields a CSM would compute by hand."""

from __future__ import annotations

import csv
from pathlib import Path

from .pack import Pack

INT_FIELDS = (
    "arr_usd", "days_to_renewal", "units_licensed", "units_active_now", "units_active_90d_ago",
    "users_now", "users_90d_ago", "tickets_90d", "sev1_tickets_90d", "invoice_days_overdue",
    "days_since_exec_meeting",
)


def _pct_change(now: int, before: int) -> float | None:
    if before == 0:
        return None
    return round((now - before) / before * 100, 1)


def _num(v: str):
    if v in ("", None):
        return None
    f = float(v)
    return int(f) if f.is_integer() and "." not in str(v) else f


def enrich(row: dict, pack: Pack) -> dict:
    c = pack.col
    rec: dict = dict(row)
    for k in INT_FIELDS:
        rec[c(k)] = int(row[c(k)])
    rec[c("last_nps")] = _num(row[c("last_nps")])
    for ef in pack.extra_fields:
        rec[ef["name"]] = _num(row[ef["name"]])
    rec[c("utilization_pct")] = round(rec[c("units_active_now")] / rec[c("units_licensed")] * 100, 1)
    rec[c("units_change_90d_pct")] = _pct_change(rec[c("units_active_now")], rec[c("units_active_90d_ago")])
    rec[c("users_change_90d_pct")] = _pct_change(rec[c("users_now")], rec[c("users_90d_ago")])
    return rec


def load_accounts(path: str | Path, pack: Pack) -> list[dict]:
    with open(path, newline="") as f:
        return [enrich(r, pack) for r in csv.DictReader(f)]


def load_labels(path: str | Path) -> dict[str, dict]:
    with open(path, newline="") as f:
        return {r["account_id"]: r for r in csv.DictReader(f)}
