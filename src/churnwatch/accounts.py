"""Load a CRM export and add the derived fields a CSM would compute by hand."""

from __future__ import annotations

import csv
from pathlib import Path

INT_FIELDS = {
    "arr_usd", "days_to_renewal", "vehicles_licensed", "vehicles_active_now",
    "vehicles_active_90d_ago", "weekly_active_users_now", "weekly_active_users_90d_ago",
    "tickets_90d", "sev1_tickets_90d", "invoice_days_overdue", "days_since_exec_meeting",
}


def _pct_change(now: int, before: int) -> float | None:
    if before == 0:
        return None
    return round((now - before) / before * 100, 1)


def enrich(row: dict) -> dict:
    rec: dict = {}
    for k, v in row.items():
        if k in INT_FIELDS:
            rec[k] = int(v)
        elif k == "last_nps":
            rec[k] = int(v) if v not in ("", None) else None
        else:
            rec[k] = v
    rec["vehicle_utilization_pct"] = round(rec["vehicles_active_now"] / rec["vehicles_licensed"] * 100, 1)
    rec["vehicles_active_change_90d_pct"] = _pct_change(rec["vehicles_active_now"], rec["vehicles_active_90d_ago"])
    rec["weekly_users_change_90d_pct"] = _pct_change(rec["weekly_active_users_now"], rec["weekly_active_users_90d_ago"])
    return rec


def load_accounts(path: str | Path) -> list[dict]:
    with open(path, newline="") as f:
        return [enrich(r) for r in csv.DictReader(f)]


def load_labels(path: str | Path) -> dict[str, dict]:
    with open(path, newline="") as f:
        return {r["account_id"]: r for r in csv.DictReader(f)}
