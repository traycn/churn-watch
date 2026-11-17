"""The structured output every scorer (LLM or baseline) must return."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Reason = Literal[
    "usage_decline",
    "champion_loss",
    "support_issues",
    "billing",
    "competitor",
    "budget_or_downsizing",
    "low_adoption",
    "none",
]
REASONS: tuple[str, ...] = Reason.__args__  # type: ignore[attr-defined]


class Evidence(BaseModel):
    field: str = Field(description="Exact column name from the account record, or a derived field name.")
    value: str | float | int = Field(
        description="The value of that field exactly as it appears in the record. "
        "For csm_notes, a verbatim quote of a few words from the notes."
    )


class Driver(BaseModel):
    driver: Reason
    explanation: str = Field(description="One sentence a CSM could say out loud.")
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Assessment(BaseModel):
    account_id: str
    at_risk: bool
    risk_score: int = Field(ge=0, le=100, description="0 = certain to renew, 100 = certain to churn.")
    main_reason: Reason = Field(description="'none' when at_risk is false.")
    drivers: list[Driver] = Field(
        max_length=3,
        description="Up to 3 drivers, most important first. Include mitigating factors as driver 'none'.",
    )
    next_step: str = Field(description="One concrete action for the CSM this week.")


def tool_schema() -> dict:
    """JSON schema for the forced tool call that carries the assessment."""
    return Assessment.model_json_schema()
