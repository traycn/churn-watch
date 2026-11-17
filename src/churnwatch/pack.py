"""Industry packs: everything that changes when churn-watch is pointed at a new industry.

A pack is a folder `packs/<slug>/` with a `pack.toml` (see `packs/_template/pack.toml`).
The engine owns the account-health model and the scenarios; a pack supplies the domain:
what the product is, what a billable unit is, the column names a CRM in that industry
would use, the customer segments, the features, the CSM-note wording for each scenario,
and the pricing.

Inside the engine every field has a canonical name (`units_active_now`). On export and
in front of the model, fields use the pack's column names (`vehicles_active_now`,
`pharmacy_locations_live`, ...). `Pack.col(canonical)` maps one to the other.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def packs_dir() -> Path:
    return Path(os.environ.get("CHURNWATCH_PACKS_DIR", ROOT / "packs"))


# canonical field -> (default column name template, default description template)
CANONICAL: dict[str, tuple[str, str]] = {
    "account_id": ("account_id", "Unique account ID."),
    "account_name": ("account_name", "Customer name."),
    "segment": ("industry", "The customer's industry or segment."),
    "region": ("region", "Sales region."),
    "plan": ("plan", "Subscription plan."),
    "arr_usd": ("arr_usd", "Annual recurring revenue in USD."),
    "customer_since": ("customer_since", "Contract start date."),
    "renewal_date": ("renewal_date", "Next renewal date."),
    "days_to_renewal": ("days_to_renewal", "Days from the export date to renewal."),
    "units_licensed": ("{units}_licensed", "Number of {units_h} the customer pays for."),
    "units_active_now": ("{units}_active_now", "{Units_h} active in the last 30 days."),
    "units_active_90d_ago": ("{units}_active_90d_ago", "{Units_h} that were active 90 days ago."),
    "users_now": ("weekly_active_users_now", "{Users_h} who used the product in the last week."),
    "users_90d_ago": ("weekly_active_users_90d_ago", "Weekly active {users_h} 90 days ago."),
    "features_adopted": ("features_adopted", "Modules in active use, separated by semicolons."),
    "tickets_90d": ("tickets_90d", "Support tickets opened in the last 90 days."),
    "sev1_tickets_90d": ("sev1_tickets_90d", "Severity-1 (outage-level) tickets in the last 90 days."),
    "open_escalation": ("open_escalation", "yes if an executive escalation is open."),
    "last_nps": ("last_nps", "Most recent NPS response, 0 to 10; blank if none."),
    "champion_status": ("champion_status", "active, or left if the main sponsor has left the customer."),
    "invoice_days_overdue": ("invoice_days_overdue", "Days the oldest unpaid invoice is past due; 0 if none."),
    "days_since_exec_meeting": ("days_since_exec_meeting", "Days since the last executive-level meeting."),
    "csm_notes": ("csm_notes", "Free-text notes from the customer success manager."),
    # derived, added at load time
    "utilization_pct": ("{unit}_utilization_pct", "Derived: {units_h} active now as a % of {units_h} licensed."),
    "units_change_90d_pct": ("{units}_active_change_90d_pct",
                             "Derived: % change in active {units_h} over 90 days; null with no history."),
    "users_change_90d_pct": ("weekly_users_change_90d_pct",
                             "Derived: % change in weekly active {users_h} over 90 days; null with no history."),
}
DERIVED = ("utilization_pct", "units_change_90d_pct", "users_change_90d_pct")

SCENARIOS = {
    # scenario: (at_risk, main_reason, ambiguous)
    "healthy": ("no", "none", False),
    "expanding": ("no", "none", False),
    "seasonal_dip": ("no", "none", True),
    "ramping": ("no", "none", True),
    "resolved_incident": ("no", "none", True),
    "champion_replaced": ("no", "none", True),
    "ap_delay": ("no", "none", True),
    "usage_decline": ("yes", "usage_decline", False),
    "champion_loss": ("yes", "champion_loss", False),
    "support_issues": ("yes", "support_issues", False),
    "billing": ("yes", "billing", False),
    "competitor": ("yes", "competitor", True),
    "downsizing": ("yes", "budget_or_downsizing", False),
    "low_adoption": ("yes", "low_adoption", False),
}

# 50 accounts: 22 at risk, 28 not; 14 ambiguous.
DEFAULT_TESTSET_MIX = {
    "healthy": 10, "expanding": 4, "seasonal_dip": 4, "ramping": 3, "resolved_incident": 3,
    "champion_replaced": 2, "ap_delay": 2, "usage_decline": 5, "champion_loss": 4,
    "support_issues": 3, "billing": 2, "competitor": 3, "downsizing": 2, "low_adoption": 3,
}
# About 18% at risk, closer to a real book of business.
DEFAULT_EXPORT_WEIGHTS = {
    "healthy": 48, "expanding": 10, "seasonal_dip": 6, "ramping": 5, "resolved_incident": 4,
    "champion_replaced": 3, "ap_delay": 3, "usage_decline": 5, "champion_loss": 4,
    "support_issues": 3, "billing": 2, "competitor": 3, "downsizing": 2, "low_adoption": 2,
}

REASON_DEFAULTS = {
    "usage_decline": "Active {units_h} or weekly users fell sharply (about 25% or more in 90 days) "
                     "and nothing in the notes explains it away.",
    "champion_loss": "The buyer or relationship owner left, and no engaged successor is named.",
    "support_issues": "Repeated severe incidents or an open escalation, and the customer is still unhappy.",
    "billing": "Invoices well overdue and signs the customer can't or won't pay (not a process delay).",
    "competitor": "Evidence of evaluating another vendor: a pilot, a price-comparison request, an RFP, "
                  "or a data-export request tied to vendor consolidation.",
    "budget_or_downsizing": "The customer is cutting {units_h}, staff or budget and wants to reduce scope.",
    "low_adoption": "A customer for 12+ months with low active use and few modules adopted.",
}


class PackError(ValueError):
    pass


@dataclass
class Pack:
    path: Path
    raw: dict = field(repr=False)

    # ---- identity -------------------------------------------------------------------
    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def meta(self) -> dict:
        return self.raw["pack"]

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def seed(self) -> int:
        return int(self.meta.get("seed", 7))

    @property
    def export_date(self) -> str:
        return str(self.meta["export_date"])

    # ---- vocabulary -----------------------------------------------------------------
    @cached_property
    def words(self) -> dict[str, str]:
        u = self.raw["unit"]
        users = self.raw.get("users", {}).get("name", "users")
        return {
            "unit": u["column"],                      # vehicle
            "units": u.get("column_plural", u["column"] + "s"),   # vehicles
            "unit_h": u["name"],                      # vehicle
            "units_h": u.get("name_plural", u["name"] + "s"),     # vehicles
            "Units_h": u.get("name_plural", u["name"] + "s").capitalize(),
            "users_h": users,
            "Users_h": users.capitalize(),
            "company": self.meta.get("vendor_name", "the vendor"),
        }

    def fmt(self, template: str, **extra) -> str:
        return template.format(**self.words, **extra)

    @cached_property
    def columns(self) -> dict[str, str]:
        overrides = self.raw.get("columns", {})
        cols = {k: overrides.get(k, self.fmt(v[0])) for k, v in CANONICAL.items()}
        if len(set(cols.values())) != len(cols):
            raise PackError(f"{self.slug}: two fields map to the same column name")
        return cols

    def col(self, canonical: str) -> str:
        return self.columns[canonical]

    @cached_property
    def descriptions(self) -> dict[str, str]:
        """Column name -> description, for the prompt's field glossary and the README."""
        overrides = self.raw.get("descriptions", {})
        out = {self.col(k): overrides.get(k, self.fmt(v[1])) for k, v in CANONICAL.items()}
        for ef in self.extra_fields:
            out[ef["name"]] = ef["description"]
        return out

    @property
    def extra_fields(self) -> list[dict]:
        return self.raw.get("extra_fields", [])

    @cached_property
    def reasons(self) -> dict[str, str]:
        overrides = self.raw.get("reasons", {})
        return {k: overrides.get(k, self.fmt(v)) for k, v in REASON_DEFAULTS.items()}

    # ---- scenario mixes ---------------------------------------------------------------
    @cached_property
    def testset_mix(self) -> dict[str, int]:
        mix = {**DEFAULT_TESTSET_MIX, **self.raw.get("testset_mix", {})}
        unknown = set(mix) - set(SCENARIOS)
        if unknown:
            raise PackError(f"{self.slug}: unknown scenarios {sorted(unknown)}")
        if mix.get("seasonal_dip") and not any(s.get("seasonal") for s in self.raw["segments"]):
            raise PackError(f"{self.slug}: seasonal_dip cases need at least one segment with seasonal = true")
        return {k: v for k, v in mix.items() if v}

    @cached_property
    def export_weights(self) -> dict[str, int]:
        w = {**DEFAULT_EXPORT_WEIGHTS, **self.raw.get("export_weights", {})}
        if not any(s.get("seasonal") for s in self.raw["segments"]):
            w["seasonal_dip"] = 0
        return {k: v for k, v in w.items() if v}

    # ---- evals ------------------------------------------------------------------------
    def runs(self, defaults: list[str]) -> list[tuple[str, str]]:
        """(model, prompt) pairs to score for this pack; 'model/prompt' strings in config."""
        items = self.raw.get("evals", {}).get("runs", defaults)
        return [tuple(i.split("/", 1)) for i in items]  # type: ignore[misc]

    def demo_run(self, default: str) -> tuple[str, str]:
        return tuple(self.raw.get("evals", {}).get("demo", default).split("/", 1))  # type: ignore[return-value]

    # ---- paths ------------------------------------------------------------------------
    @property
    def export_csv(self) -> Path:
        return self.path / "data" / "accounts.csv"

    @property
    def testset_csv(self) -> Path:
        return self.path / "data" / "testset" / "accounts.csv"

    @property
    def labels_draft_csv(self) -> Path:
        return self.path / "data" / "testset" / "labels_draft.csv"

    @property
    def labels_csv(self) -> Path:
        return self.path / "data" / "testset" / "labels.csv"

    @property
    def evals_dir(self) -> Path:
        return self.path / "evals"

    @property
    def readme(self) -> Path:
        return self.path / "README.md"


def load_pack(slug_or_path: str | Path) -> Pack:
    p = Path(slug_or_path)
    if not p.exists():
        p = packs_dir() / str(slug_or_path)
    f = p / "pack.toml"
    if not f.exists():
        raise PackError(f"No pack.toml in {p}. Packs available: {', '.join(list_slugs()) or 'none'}")
    with f.open("rb") as fh:
        raw = tomllib.load(fh)
    for key in ("pack", "unit", "segments", "plans", "features", "notes"):
        if key not in raw:
            raise PackError(f"{p.name}: pack.toml is missing [{key}]")
    pack = Pack(p.resolve(), raw)
    missing = [s for s in pack.testset_mix if s not in raw["notes"]]
    if missing:
        raise PackError(f"{pack.slug}: no [notes] for scenarios {missing}")
    return pack


def list_slugs() -> list[str]:
    d = packs_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "pack.toml").exists() and not p.name.startswith("_"))


def all_packs() -> list[Pack]:
    return [load_pack(s) for s in list_slugs()]
