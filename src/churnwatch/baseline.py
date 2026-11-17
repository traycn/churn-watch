"""A rules model a CS ops team could write in an afternoon.

It only sees the numbers, never the CSM notes, and works for any pack through the
pack's column names. It is the bar the LLM has to beat, and the comparison shows where
the LLM adds value.
"""

from __future__ import annotations

from .pack import Pack
from .schema import Assessment, Driver, Evidence

# (reason, test on canonical values, canonical evidence fields, explanation template)
RULES = [
    ("support_issues", lambda v: v["sev1_tickets_90d"] >= 2 or v["open_escalation"] == "yes",
     ["sev1_tickets_90d", "open_escalation"], "Repeated severe tickets or an open escalation."),
    ("billing", lambda v: v["invoice_days_overdue"] >= 45,
     ["invoice_days_overdue"], "Invoices are more than 45 days overdue."),
    ("champion_loss", lambda v: v["champion_status"] == "left",
     ["champion_status"], "The champion has left."),
    ("usage_decline", lambda v: (v["units_change_90d_pct"] or 0) <= -25,
     ["units_change_90d_pct"], "Active {units_h} fell 25% or more in 90 days."),
    ("low_adoption", lambda v: v["utilization_pct"] < 50,
     ["utilization_pct"], "Fewer than half of licensed {units_h} are active."),
]


def assess(record: dict, pack: Pack) -> Assessment:
    v = {k: record[pack.col(k)] for k in
         ("sev1_tickets_90d", "open_escalation", "invoice_days_overdue", "champion_status",
          "units_change_90d_pct", "utilization_pct")}
    hits = [rule for rule in RULES if rule[1](v)]
    drivers = [
        Driver(driver=reason, explanation=pack.fmt(text),
               evidence=[Evidence(field=pack.col(f), value=v[f]) for f in fields if v[f] is not None])
        for reason, _, fields, text in hits[:3]
    ]
    return Assessment(
        account_id=record[pack.col("account_id")],
        at_risk=bool(hits),
        risk_score=min(100, 30 * len(hits)),
        main_reason=hits[0][0] if hits else "none",
        drivers=drivers,
        next_step="Schedule a health check call." if hits else "No action needed.",
    )
