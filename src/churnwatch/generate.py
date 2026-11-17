"""Generate a pack's synthetic CRM export and labeled test set.

Everything written here is made up. Each account is drawn from a hidden scenario
(healthy, usage decline, champion left, seasonal dip, ...). The engine decides how each
scenario moves the numbers; the pack supplies the vocabulary and the CSM-note wording.
Some scenarios are ambiguous by design: the numbers point one way and the notes the other.

Writes, inside packs/<slug>/data/:
  accounts.csv                demo export (default 200 accounts)
  testset/accounts.csv        evaluation set (default 50 accounts)
  testset/labels_draft.csv    draft labels from the scenario, for a person to review
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta

from .pack import CANONICAL, DERIVED, SCENARIOS, Pack

EXPORTED = [k for k in CANONICAL if k not in DERIVED]


def _note(rng: random.Random, pack: Pack, scenario: str, kw: dict) -> str:
    return pack.fmt(rng.choice(pack.raw["notes"][scenario]), **kw)


def make_account(rng: random.Random, pack: Pack, idx: int, scenario: str, prefix: str,
                 used_names: set[str]) -> dict:
    export_date = date.fromisoformat(pack.export_date)
    segments = pack.raw["segments"]
    pool = [s for s in segments if s.get("seasonal")] if scenario == "seasonal_dip" else segments
    seg = rng.choice(pool)
    prefixes = pack.raw["naming"]["prefixes"]
    tails = pack.raw["naming"].get("company_suffixes", ["Co.", "Group", "LLC", "Inc.", "Partners", "& Sons"])
    for attempt in range(200):
        name = f"{rng.choice(prefixes)} {rng.choice(seg['name_suffixes'])}"
        if attempt >= 40:  # small name space: add a company suffix
            name = f"{name} {rng.choice(tails)}"
        if name not in used_names:
            break
    else:
        name = f"{name} {idx}"
    used_names.add(name)

    unit = pack.raw["unit"]
    licensed = rng.choice(unit["counts"])
    plans = [p for p in pack.raw["plans"] if p.get("min_units", 0) <= licensed] or pack.raw["plans"][:1]
    plan = rng.choices(plans, [p.get("weight", 1) for p in plans])[0]
    arr = int(round(licensed * plan["price_per_unit"] * rng.uniform(0.9, 1.1), -2))

    feats_cfg = pack.raw["features"]
    optional = feats_cfg.get("optional", [])
    feats = [feats_cfg["core"]] + rng.sample(optional, min(len(optional), rng.randint(2, 4)))

    tenure_days = rng.randint(400, 2200)
    days_to_renewal = rng.randint(20, 330)
    nps: int | None = rng.randint(8, 10)
    champion, overdue, escalation = "active", 0, "no"
    tickets, sev1 = rng.randint(0, 4), 0
    exec_days = rng.randint(20, 120)
    ratio_now, trend = rng.uniform(0.84, 0.97), rng.uniform(0.96, 1.04)  # trend = now / 90d ago

    contacts = pack.raw["naming"]["contacts"]
    a = rng.choice(contacts)
    b = rng.choice([c for c in contacts if c != a] or contacts)
    kw = dict(a=a, b=b, n=rng.randint(2, 12),
              d1=(export_date - timedelta(days=rng.randint(5, 40))).strftime("%b %d"))

    if scenario == "expanding":
        ratio_now, trend, nps = rng.uniform(0.95, 1.0), rng.uniform(1.10, 1.30), rng.randint(9, 10)
    elif scenario == "seasonal_dip":
        trend, ratio_now, nps = rng.uniform(0.50, 0.70), rng.uniform(0.45, 0.62), rng.randint(7, 9)
        days_to_renewal = rng.randint(120, 300)
    elif scenario == "ramping":
        tenure_days = rng.randint(45, 100)
        ratio_now, trend, nps = rng.uniform(0.30, 0.50), rng.uniform(2.0, 4.0), None
        days_to_renewal = 365 - tenure_days
        feats = [feats_cfg["core"]] + optional[:1]
        kw["d1"] = (export_date + timedelta(days=rng.randint(10, 40))).strftime("%b %d")
    elif scenario == "resolved_incident":
        tickets, sev1, nps = rng.randint(8, 14), rng.randint(2, 3), rng.randint(6, 8)
    elif scenario == "champion_replaced":
        champion, exec_days = "left", rng.randint(10, 40)
    elif scenario == "ap_delay":
        overdue = rng.randint(48, 75)
    elif scenario == "usage_decline":
        trend, ratio_now = rng.uniform(0.45, 0.70), rng.uniform(0.40, 0.60)
        nps, exec_days = rng.choice([None, 4, 5, 6]), rng.randint(120, 260)
        days_to_renewal = rng.randint(30, 150)
    elif scenario == "champion_loss":
        champion, trend, ratio_now, nps = "left", rng.uniform(0.82, 0.95), rng.uniform(0.70, 0.85), None
        exec_days, days_to_renewal = rng.randint(150, 300), rng.randint(40, 120)
    elif scenario == "support_issues":
        tickets, sev1, escalation = rng.randint(11, 22), rng.randint(2, 4), "yes"
        nps, trend = rng.randint(1, 4), rng.uniform(0.85, 0.98)
    elif scenario == "billing":
        overdue, trend = rng.randint(62, 120), rng.uniform(0.85, 0.97)
        nps, exec_days = rng.choice([None, 5, 6]), rng.randint(90, 200)
    elif scenario == "competitor":
        ratio_now, trend, nps = rng.uniform(0.78, 0.90), rng.uniform(0.92, 1.0), rng.choice([6, 7])
        days_to_renewal = rng.randint(45, 150)
    elif scenario == "downsizing":
        ratio_now, trend, nps = rng.uniform(0.55, 0.70), rng.uniform(0.65, 0.80), rng.randint(6, 8)
        days_to_renewal = rng.randint(30, 120)
    elif scenario == "low_adoption":
        tenure_days, ratio_now, trend = rng.randint(380, 900), rng.uniform(0.22, 0.42), rng.uniform(0.95, 1.05)
        nps, feats, exec_days = None, [feats_cfg["core"]], rng.randint(200, 400)

    active_now = max(1, round(licensed * ratio_now))
    active_90 = min(licensed, max(1, round(active_now / trend)))
    lo, hi = unit.get("users_per_unit", [1.0, 1.0])
    users_now = max(1, round(active_now * rng.uniform(lo, hi)))
    users_90 = max(1, round(users_now / (trend * rng.uniform(0.95, 1.05))))
    if tenure_days < 90:  # not yet a customer 90 days ago
        active_90, users_90 = 0, 0

    rec = {
        "account_id": f"{prefix}-{idx:04d}",
        "account_name": name,
        "segment": seg["name"],
        "region": rng.choice(pack.raw.get("regions", ["West", "Mountain", "Central", "Southeast", "Northeast"])),
        "plan": plan["name"],
        "arr_usd": arr,
        "customer_since": (export_date - timedelta(days=tenure_days)).isoformat(),
        "renewal_date": (export_date + timedelta(days=days_to_renewal)).isoformat(),
        "days_to_renewal": days_to_renewal,
        "units_licensed": licensed,
        "units_active_now": active_now,
        "units_active_90d_ago": active_90,
        "users_now": users_now,
        "users_90d_ago": users_90,
        "features_adopted": ";".join(feats),
        "tickets_90d": tickets,
        "sev1_tickets_90d": sev1,
        "open_escalation": escalation,
        "last_nps": "" if nps is None else nps,
        "champion_status": champion,
        "invoice_days_overdue": overdue,
        "days_since_exec_meeting": exec_days,
        "csm_notes": _note(rng, pack, scenario, kw),
    }
    out = {pack.col(k): v for k, v in rec.items()}
    for ef in pack.extra_fields:
        lo_, hi_ = ef.get("by_scenario", {}).get(scenario, ef["base"])
        out[ef["name"]] = round(rng.uniform(lo_, hi_), ef.get("decimals", 0)) if ef.get("decimals") \
            else int(round(rng.uniform(lo_, hi_)))
    out["_scenario"] = scenario
    return out


def _write(path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def export_columns(pack: Pack) -> list[str]:
    cols = [pack.col(k) for k in EXPORTED]
    notes = cols.pop()  # keep notes last
    return cols + [ef["name"] for ef in pack.extra_fields] + [notes]


def generate(pack: Pack, export_size: int | None = None, testset_size_check: bool = True) -> dict:
    rng = random.Random(pack.seed)
    sizes = pack.raw.get("sizes", {})
    n_export = export_size or sizes.get("export", 200)
    cols = export_columns(pack)

    used: set[str] = set()
    names, weights = list(pack.export_weights), list(pack.export_weights.values())
    export = [make_account(rng, pack, i + 1, rng.choices(names, weights)[0], "ACC", used) for i in range(n_export)]
    _write(pack.export_csv, export, cols)

    plan = [s for s, n in pack.testset_mix.items() for _ in range(n)]
    rng.shuffle(plan)
    test = [make_account(rng, pack, i + 1, s, "TST", used) for i, s in enumerate(plan)]
    _write(pack.testset_csv, test, cols)

    labels = []
    for acc in test:
        at_risk, reason, ambiguous = SCENARIOS[acc["_scenario"]]
        labels.append({"account_id": acc[pack.col("account_id")], "at_risk": at_risk, "main_reason": reason,
                       "ambiguous": "yes" if ambiguous else "no", "generator_scenario": acc["_scenario"],
                       "reviewed": "no", "reviewer_note": ""})
    _write(pack.labels_draft_csv, labels, list(labels[0]))
    return {"export": len(export), "testset": len(test),
            "at_risk": sum(1 for l in labels if l["at_risk"] == "yes"),
            "ambiguous": sum(1 for l in labels if l["ambiguous"] == "yes")}
