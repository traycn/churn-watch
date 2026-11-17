"""A rules model a CS ops team could write in an afternoon.

It only sees the numbers, never the CSM notes. It is here so the README can show
whether the LLM adds anything over simple thresholds, and where.
"""

from __future__ import annotations

from .schema import Assessment, Driver, Evidence

RULES = [
    # (reason, test, evidence fields, explanation)
    ("support_issues",
     lambda r: r["sev1_tickets_90d"] >= 2 or r["open_escalation"] == "yes",
     ["sev1_tickets_90d", "open_escalation"], "Repeated severe tickets or an open escalation."),
    ("billing",
     lambda r: r["invoice_days_overdue"] >= 45,
     ["invoice_days_overdue"], "Invoices are more than 45 days overdue."),
    ("champion_loss",
     lambda r: r["champion_status"] == "left",
     ["champion_status"], "The champion has left."),
    ("usage_decline",
     lambda r: (r["vehicles_active_change_90d_pct"] or 0) <= -25,
     ["vehicles_active_change_90d_pct"], "Active vehicles fell 25% or more in 90 days."),
    ("low_adoption",
     lambda r: r["vehicle_utilization_pct"] < 50,
     ["vehicle_utilization_pct"], "Fewer than half of licensed vehicles are active."),
]


def assess(record: dict) -> Assessment:
    hits = [rule for rule in RULES if rule[1](record)]
    drivers = [
        Driver(driver=reason, explanation=text,
               evidence=[Evidence(field=f, value=record[f]) for f in fields if record[f] is not None])
        for reason, _, fields, text in hits[:3]
    ]
    at_risk = bool(hits)
    return Assessment(
        account_id=record["account_id"],
        at_risk=at_risk,
        risk_score=min(100, 30 * len(hits)),
        main_reason=hits[0][0] if hits else "none",
        drivers=drivers,
        next_step="Schedule a health check call." if at_risk else "No action needed.",
    )
