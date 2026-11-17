"""Generate a synthetic CRM export for a fleet-telematics SaaS company.

Everything this script writes is made up. No real customer data is used.

Each account is drawn from a hidden *scenario* (healthy, usage decline, champion
left, seasonal dip, ...). The scenario drives both the numbers and the free-text
CSM notes. Some scenarios are deliberately ambiguous: the numbers look risky but
the notes explain why the account is fine, or the numbers look fine but the
notes reveal a risk. Those are the cases a rules model gets wrong.

Outputs (deterministic for a given --seed):
  data/accounts.csv              200-account demo export
  data/testset/accounts.csv      50-account evaluation set
  data/testset/labels_draft.csv  draft labels derived from the scenario,
                                 to be reviewed by a person (see docs/labeling-rubric.md)

Usage:
  python data/generate.py --seed 7
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

EXPORT_DATE = date(2026, 11, 16)
ROOT = Path(__file__).resolve().parent

COLUMNS = [
    "account_id", "account_name", "industry", "region", "plan", "arr_usd",
    "customer_since", "renewal_date", "days_to_renewal",
    "vehicles_licensed", "vehicles_active_now", "vehicles_active_90d_ago",
    "weekly_active_users_now", "weekly_active_users_90d_ago",
    "features_adopted", "tickets_90d", "sev1_tickets_90d", "open_escalation",
    "last_nps", "champion_status", "invoice_days_overdue",
    "days_since_exec_meeting", "csm_notes",
]

# scenario -> (at_risk, main_reason, ambiguous)
SCENARIOS = {
    "healthy":            ("no",  "none",            False),
    "expanding":          ("no",  "none",            False),
    "seasonal_dip":       ("no",  "none",            True),
    "ramping":            ("no",  "none",            True),
    "resolved_incident":  ("no",  "none",            True),
    "champion_replaced":  ("no",  "none",            True),
    "ap_delay":           ("no",  "none",            True),
    "usage_decline":      ("yes", "usage_decline",   False),
    "champion_loss":      ("yes", "champion_loss",   False),
    "support_issues":     ("yes", "support_issues",  False),
    "billing":            ("yes", "billing",         False),
    "competitor":         ("yes", "competitor",      True),
    "downsizing":         ("yes", "budget_or_downsizing", False),
    "low_adoption":       ("yes", "low_adoption",    False),
}

# Fixed mix for the 50-account test set: 22 at risk, 28 not; 14 ambiguous.
TESTSET_MIX = {
    "healthy": 10, "expanding": 4, "seasonal_dip": 4, "ramping": 3,
    "resolved_incident": 3, "champion_replaced": 2, "ap_delay": 2,
    "usage_decline": 5, "champion_loss": 4, "support_issues": 3, "billing": 2,
    "competitor": 3, "downsizing": 2, "low_adoption": 3,
}

# Weights for the 200-account demo export (about 18% at risk, closer to a real book).
EXPORT_WEIGHTS = {
    "healthy": 48, "expanding": 10, "seasonal_dip": 6, "ramping": 5,
    "resolved_incident": 4, "champion_replaced": 3, "ap_delay": 3,
    "usage_decline": 5, "champion_loss": 4, "support_issues": 3, "billing": 2,
    "competitor": 3, "downsizing": 2, "low_adoption": 2,
}

INDUSTRIES = ["Trucking", "Construction", "Field services", "Food distribution",
              "Waste management", "Utilities", "Public sector", "Landscaping",
              "Oil and gas", "Passenger transport"]
SEASONAL_INDUSTRIES = ["Landscaping", "Construction"]
REGIONS = ["West", "Mountain", "Central", "Southeast", "Northeast"]
FEATURES = ["gps_tracking", "eld", "dashcams", "safety_coaching", "routing",
            "maintenance", "fuel_reporting"]
FIRST_NAMES = ["Dana", "Marcus", "Priya", "Luis", "Keisha", "Tom", "Aiko", "Rafael",
               "Jen", "Omar", "Beth", "Nikhil", "Carla", "Dev", "Hannah", "Victor",
               "Mei", "Andre", "Sofia", "Grant"]
NAME_A = ["Redwood", "Granite", "Bluewater", "Summit", "Prairie", "Ironline", "Cedar",
          "Harbor", "Copper", "Lakeside", "Northgate", "Sunridge", "Valley", "Keystone",
          "Silverpine", "Mesa", "Riverbend", "Oakmont", "Highland", "Clearfield",
          "Bayshore", "Stonebridge", "Westfork", "Pinecrest", "Canyon"]
NAME_B = {
    "Trucking": ["Freight", "Haulage", "Logistics", "Carriers"],
    "Construction": ["Builders", "Construction", "Contracting", "Earthworks"],
    "Field services": ["Services", "Field Ops", "Mechanical", "HVAC"],
    "Food distribution": ["Foods", "Provisions", "Fresh Supply", "Distributors"],
    "Waste management": ["Disposal", "Recycling", "Waste Services", "Environmental"],
    "Utilities": ["Power", "Water District", "Energy", "Utility Co-op"],
    "Public sector": ["County Fleet", "City Services", "Transit Authority", "Public Works"],
    "Landscaping": ["Landscaping", "Grounds", "Lawn & Tree", "Outdoor"],
    "Oil and gas": ["Energy Services", "Oilfield", "Pipeline", "Petroleum"],
    "Passenger transport": ["Coach Lines", "Shuttle", "Transit", "Charters"],
}


@dataclass
class Account:
    account_id: str
    account_name: str
    industry: str
    region: str
    plan: str
    arr_usd: int
    customer_since: str
    renewal_date: str
    days_to_renewal: int
    vehicles_licensed: int
    vehicles_active_now: int
    vehicles_active_90d_ago: int
    weekly_active_users_now: int
    weekly_active_users_90d_ago: int
    features_adopted: str
    tickets_90d: int
    sev1_tickets_90d: int
    open_escalation: str
    last_nps: str
    champion_status: str
    invoice_days_overdue: int
    days_since_exec_meeting: int
    csm_notes: str
    scenario: str = field(default="", repr=False)


def pick_notes(rng: random.Random, options: list[str], **kw) -> str:
    return rng.choice(options).format(**kw)


NOTES = {
    "healthy": [
        "QBR on {d1}. {a} happy with driver safety scores; asked for a routing demo next quarter.",
        "Quiet account. {a} answers emails within a day. No open asks.",
        "{a} presented our safety numbers to their insurer and got a premium reduction. Strong reference candidate.",
        "Renewal conversation started early; {a} said budget is approved.",
        "",
    ],
    "expanding": [
        "{a} added {n} trucks from an acquisition and wants them on the platform by January.",
        "Asked for a quote on dashcams for the whole fleet after the pilot went well. {a} is the sponsor.",
        "New depot opening in Q1; {a} wants {n} more licenses and the maintenance module.",
    ],
    "seasonal_dip": [
        "Seasonal: crews park most trucks Nov-Mar every year. Same dip last winter and they renewed early. {a} confirmed spring ramp.",
        "Winter slowdown as expected for this business. {a} said 'see you in March'. Usage pattern matches last year.",
        "Half the fleet is laid up for the off-season, normal for them. {a} still logs in weekly for the yard vehicles.",
    ],
    "ramping": [
        "Onboarding in progress: phase 2 of 3 rollout scheduled for {d1}. {a} is driving it; on track.",
        "Signed recently. Driver app rollout is staged by depot; 2 of 4 depots live. Kickoff went well.",
        "Still installing gateways (backordered hardware arrived last week). {a} expects full fleet live by mid-December.",
    ],
    "resolved_incident": [
        "Sev1 on gateway firmware in October caused GPS gaps. Root-caused and fixed {d1}; {a} thanked the team on the follow-up call.",
        "Rough October: two outages hit their region. Both resolved, credits issued, {a} said trust is restored.",
        "Escalation closed. Ticket volume was from the ELD migration; all resolved and {a} signed off.",
    ],
    "champion_replaced": [
        "Former fleet director left in September. New director {a} attended the QBR and wants to expand dashcams.",
        "Champion moved to another company. Successor {a} already completed admin training and is very engaged.",
    ],
    "ap_delay": [
        "Invoices late because their AP moved to a new ERP; every vendor is delayed. {a} confirmed PO is approved and payment is queued.",
        "Payment delay is a finance-system migration on their side, not a dispute. {a} sent the remittance schedule.",
    ],
    "usage_decline": [
        "Couldn't get {a} on a call this quarter. Drivers reportedly 'stopped bothering' with the app.",
        "{a} mentioned the dispatch team went back to spreadsheets. Tried to schedule retraining, no reply.",
        "Usage dropping since summer. {a} says the platform 'is fine' but won't commit to a QBR.",
        "",
    ],
    "champion_loss": [
        "Our champion {b} left in August. No response from the new ops lead. Renewal coming up.",
        "{b} (VP Ops, signed the deal) retired. Haven't identified anyone who owns the relationship now.",
        "Champion {b} left. The new manager came from a company that used a different telematics vendor.",
    ],
    "support_issues": [
        "{a} furious about repeated dashcam upload failures; open escalation for 5 weeks. Said 'we're evaluating options' if not fixed.",
        "Multiple sev1s on ELD sync. {a} has escalated to their CFO. Fix ETA keeps slipping.",
        "Ongoing data gaps on trailers. {a} says drivers don't trust the hours data anymore.",
    ],
    "billing": [
        "Two invoices unpaid. {a} mentioned cash flow is tight and asked about pausing the contract.",
        "Finance not responding to collections. {a} said the owner is 'reviewing all subscriptions'.",
    ],
    "competitor": [
        "Saw another vendor's dashcams on {n} of their trucks during the site visit. {a} says it's 'just a test'.",
        "Procurement asked for an itemized price breakdown 'for comparison'. {a} hinted an RFP goes out in Q1.",
        "{a} asked how hard it would be to export all historical data. New CIO is consolidating vendors.",
    ],
    "downsizing": [
        "Company lost a major contract and is cutting about a third of drivers. {a} asked to reduce licenses at renewal.",
        "Budget freeze announced. {a} asked which modules they could drop and whether we offer month-to-month.",
    ],
    "low_adoption": [
        "Never finished the driver app rollout after kickoff. {a} is busy; only the yard trucks use it.",
        "Bought the full bundle but only GPS is in use. Repeated offers of training declined.",
        "",
    ],
}


def make_account(rng: random.Random, idx: int, scenario: str, prefix: str) -> Account:
    industry = rng.choice(SEASONAL_INDUSTRIES if scenario == "seasonal_dip" else INDUSTRIES)
    name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B[industry])}"
    licensed = rng.choice([12, 18, 25, 30, 40, 55, 60, 75, 90, 120, 150, 200, 260, 340, 480, 650])
    plan = "Enterprise" if licensed >= 150 else rng.choice(["Essentials", "Essentials", "Enterprise"])
    per_vehicle = 520 if plan == "Enterprise" else 360
    arr = int(round(licensed * per_vehicle * rng.uniform(0.9, 1.1), -2))

    tenure_days = rng.randint(400, 2200)
    days_to_renewal = rng.randint(20, 330)
    nps: int | None = rng.randint(8, 10)
    champion = "active"
    overdue = 0
    tickets = rng.randint(0, 4)
    sev1 = 0
    escalation = "no"
    exec_days = rng.randint(20, 120)
    feats = ["gps_tracking"] + rng.sample(FEATURES[1:], rng.randint(2, 4))

    ratio_now = rng.uniform(0.84, 0.97)
    trend = rng.uniform(0.96, 1.04)  # now / 90d ago

    a = rng.choice(FIRST_NAMES)
    b = rng.choice([n for n in FIRST_NAMES if n != a])
    note_kw = dict(a=a, b=b, n=rng.randint(4, 30),
                   d1=(EXPORT_DATE - timedelta(days=rng.randint(5, 40))).strftime("%b %d"))
    if scenario == "ramping":  # rollout milestones are in the future
        note_kw["d1"] = (EXPORT_DATE + timedelta(days=rng.randint(10, 40))).strftime("%b %d")

    if scenario == "expanding":
        ratio_now = rng.uniform(0.95, 1.0)
        trend = rng.uniform(1.10, 1.30)
        nps = rng.randint(9, 10)
    elif scenario == "seasonal_dip":
        trend = rng.uniform(0.50, 0.70)
        ratio_now = rng.uniform(0.45, 0.62)
        nps = rng.randint(7, 9)
        days_to_renewal = rng.randint(120, 300)
    elif scenario == "ramping":
        tenure_days = rng.randint(45, 100)
        ratio_now = rng.uniform(0.30, 0.50)
        trend = rng.uniform(2.0, 4.0)
        nps = None
        days_to_renewal = 365 - tenure_days
        feats = ["gps_tracking", "eld"]
    elif scenario == "resolved_incident":
        tickets = rng.randint(8, 14)
        sev1 = rng.randint(2, 3)
        nps = rng.randint(6, 8)
    elif scenario == "champion_replaced":
        champion = "left"
        exec_days = rng.randint(10, 40)
    elif scenario == "ap_delay":
        overdue = rng.randint(48, 75)
    elif scenario == "usage_decline":
        trend = rng.uniform(0.45, 0.70)
        ratio_now = rng.uniform(0.40, 0.60)
        nps = rng.choice([None, 4, 5, 6])
        exec_days = rng.randint(120, 260)
        days_to_renewal = rng.randint(30, 150)
    elif scenario == "champion_loss":
        champion = "left"
        trend = rng.uniform(0.82, 0.95)
        ratio_now = rng.uniform(0.70, 0.85)
        nps = None
        exec_days = rng.randint(150, 300)
        days_to_renewal = rng.randint(40, 120)
    elif scenario == "support_issues":
        tickets = rng.randint(11, 22)
        sev1 = rng.randint(2, 4)
        escalation = "yes"
        nps = rng.randint(1, 4)
        trend = rng.uniform(0.85, 0.98)
    elif scenario == "billing":
        overdue = rng.randint(62, 120)
        trend = rng.uniform(0.85, 0.97)
        nps = rng.choice([None, 5, 6])
        exec_days = rng.randint(90, 200)
    elif scenario == "competitor":
        ratio_now = rng.uniform(0.78, 0.90)
        trend = rng.uniform(0.92, 1.0)
        nps = rng.choice([6, 7])
        days_to_renewal = rng.randint(45, 150)
    elif scenario == "downsizing":
        ratio_now = rng.uniform(0.55, 0.70)
        trend = rng.uniform(0.65, 0.80)
        nps = rng.randint(6, 8)
        days_to_renewal = rng.randint(30, 120)
    elif scenario == "low_adoption":
        tenure_days = rng.randint(380, 900)
        ratio_now = rng.uniform(0.22, 0.42)
        trend = rng.uniform(0.95, 1.05)
        nps = None
        feats = ["gps_tracking"]
        exec_days = rng.randint(200, 400)

    active_now = max(1, round(licensed * ratio_now))
    active_90 = max(0 if scenario == "ramping" else 1, round(active_now / trend))
    active_90 = min(active_90, licensed)
    if scenario == "ramping":
        active_90 = max(0, round(active_now / trend))
    users_ratio = rng.uniform(0.08, 0.15)
    wau_now = max(1, round(licensed * users_ratio * (active_now / licensed) / 0.9))
    wau_90 = max(1, round(wau_now / (trend * rng.uniform(0.95, 1.05))))
    if tenure_days < 90:  # not yet a customer 90 days ago
        active_90, wau_90 = 0, 0

    customer_since = EXPORT_DATE - timedelta(days=tenure_days)
    renewal = EXPORT_DATE + timedelta(days=days_to_renewal)

    return Account(
        account_id=f"{prefix}-{idx:04d}",
        account_name=name,
        industry=industry,
        region=rng.choice(REGIONS),
        plan=plan,
        arr_usd=arr,
        customer_since=customer_since.isoformat(),
        renewal_date=renewal.isoformat(),
        days_to_renewal=days_to_renewal,
        vehicles_licensed=licensed,
        vehicles_active_now=active_now,
        vehicles_active_90d_ago=active_90,
        weekly_active_users_now=wau_now,
        weekly_active_users_90d_ago=wau_90,
        features_adopted=";".join(feats),
        tickets_90d=tickets,
        sev1_tickets_90d=sev1,
        open_escalation=escalation,
        last_nps="" if nps is None else str(nps),
        champion_status=champion,
        invoice_days_overdue=overdue,
        days_since_exec_meeting=exec_days,
        csm_notes=pick_notes(rng, NOTES[scenario], **note_kw),
        scenario=scenario,
    )


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # 200-account demo export
    names = list(EXPORT_WEIGHTS)
    weights = list(EXPORT_WEIGHTS.values())
    export = [make_account(rng, i + 1, rng.choices(names, weights)[0], "ACC")
              for i in range(200)]
    write_csv(ROOT / "accounts.csv", [asdict(a) for a in export], COLUMNS)

    # 50-account test set with a fixed mix, shuffled so scenario order leaks nothing
    plan = [s for s, n in TESTSET_MIX.items() for _ in range(n)]
    rng.shuffle(plan)
    test = [make_account(rng, i + 1, s, "TST") for i, s in enumerate(plan)]
    write_csv(ROOT / "testset" / "accounts.csv", [asdict(a) for a in test], COLUMNS)

    labels = []
    for a in test:
        at_risk, reason, ambiguous = SCENARIOS[a.scenario]
        labels.append({
            "account_id": a.account_id,
            "at_risk": at_risk,
            "main_reason": reason,
            "ambiguous": "yes" if ambiguous else "no",
            "generator_scenario": a.scenario,
            "reviewed": "no",
            "reviewer_note": "",
        })
    write_csv(ROOT / "testset" / "labels_draft.csv", labels, list(labels[0]))
    print(f"wrote {len(export)} export accounts and {len(test)} test accounts")


if __name__ == "__main__":
    main()
