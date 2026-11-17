from types import SimpleNamespace

import pytest

from churnwatch import baseline
from churnwatch.accounts import enrich, load_accounts
from churnwatch.grounding import check_evidence, grounding
from churnwatch.budget import Budget
from churnwatch.llm import CacheMiss, ClaudeScorer
from churnwatch.schema import Assessment, Evidence

ROW = {
    "account_id": "T-1", "account_name": "Test Freight", "industry": "Trucking", "region": "West",
    "plan": "Essentials", "arr_usd": "10000", "customer_since": "2023-01-01", "renewal_date": "2027-01-01",
    "days_to_renewal": "46", "vehicles_licensed": "40", "vehicles_active_now": "20",
    "vehicles_active_90d_ago": "36", "weekly_active_users_now": "3", "weekly_active_users_90d_ago": "6",
    "features_adopted": "gps_tracking;eld", "tickets_90d": "2", "sev1_tickets_90d": "0",
    "open_escalation": "no", "last_nps": "", "champion_status": "active", "invoice_days_overdue": "0",
    "days_since_exec_meeting": "200",
    "csm_notes": "Dispatch team went back to spreadsheets. Tried to schedule retraining, no reply.",
}


PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50}}


def _budget(tmp_path, cap=25.0):
    return Budget(cap_usd=cap, prices=PRICES, ledger=tmp_path / "spend.csv")


@pytest.fixture
def rec():
    return enrich(ROW)


def test_enrich_derives_fields(rec):
    assert rec["vehicle_utilization_pct"] == 50.0
    assert rec["vehicles_active_change_90d_pct"] == -44.4
    assert rec["weekly_users_change_90d_pct"] == -50.0
    assert rec["last_nps"] is None


def test_enrich_handles_no_history():
    r = enrich({**ROW, "vehicles_active_90d_ago": "0", "weekly_active_users_90d_ago": "0"})
    assert r["vehicles_active_change_90d_pct"] is None


@pytest.mark.parametrize("field,value,ok", [
    ("vehicles_active_now", 20, True),
    ("vehicles_active_now", "20", True),
    ("vehicles_active_change_90d_pct", "-44.4%", True),
    ("vehicles_active_change_90d_pct", -44, True),      # within rounding
    ("vehicles_active_change_90d_pct", -38, False),     # made-up number
    ("champion_status", "Active", True),
    ("champion_status", "left", False),
    ("csm_notes", "went back to spreadsheets", True),
    ("csm_notes", "'Went back to  spreadsheets.'", True),
    ("csm_notes", "customer is angry", False),           # not in the notes
    ("not_a_field", 1, False),
    ("last_nps", "none", True),
    ("last_nps", 5, False),
])
def test_check_evidence(rec, field, value, ok):
    assert check_evidence(Evidence(field=field, value=value), rec) is ok


def test_baseline_flags_usage_decline(rec):
    a = baseline.assess(rec)
    assert a.at_risk and a.main_reason == "usage_decline"
    verified, total, _ = grounding(a, rec)
    assert verified == total > 0


def test_baseline_healthy_account():
    r = enrich({**ROW, "vehicles_active_now": "38", "vehicles_active_90d_ago": "37"})
    a = baseline.assess(r)
    assert not a.at_risk and a.main_reason == "none" and a.drivers == []


def test_testset_loads():
    recs = load_accounts("data/testset/accounts.csv")
    assert len(recs) == 50
    assert len({r["account_id"] for r in recs}) == 50


class FakeClient:
    """Stands in for anthropic.Anthropic and returns a fixed tool call."""

    def __init__(self, tool_input):
        self.calls = 0
        self.tool_input = tool_input
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["tool_choice"] == {"type": "tool", "name": "record_assessment"}
        block = SimpleNamespace(type="tool_use", input=self.tool_input)
        return SimpleNamespace(content=[block], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=900, output_tokens=150))


GOOD = {
    "at_risk": True, "risk_score": 82, "main_reason": "usage_decline",
    "drivers": [{"driver": "usage_decline", "explanation": "Active vehicles fell by 44% in 90 days.",
                 "evidence": [{"field": "vehicles_active_change_90d_pct", "value": -44.4},
                              {"field": "csm_notes", "value": "went back to spreadsheets"}]}],
    "next_step": "Offer a retraining session to dispatch.",
}


def test_scorer_caches_and_replays(rec, tmp_path):
    client = FakeClient(GOOD)
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=client, cache_dir=tmp_path, budget=_budget(tmp_path))
    r1 = s.score(rec)
    r2 = s.score(rec)
    assert client.calls == 1 and not r1.cached and r2.cached
    assert r1.assessment.main_reason == "usage_decline"
    replay = ClaudeScorer("claude-haiku-5-5", "v2", replay=True, cache_dir=tmp_path)
    assert replay.score(rec).assessment.risk_score == 82
    with pytest.raises(CacheMiss):
        ClaudeScorer("claude-haiku-5-5", "v1", replay=True, cache_dir=tmp_path).score(rec)


def test_scorer_reports_schema_errors(rec, tmp_path):
    bad = {**GOOD, "main_reason": "vibes"}
    res = ClaudeScorer("claude-haiku-5-5", "v2", client=FakeClient(bad), cache_dir=tmp_path,
                       budget=_budget(tmp_path)).score(rec)
    assert res.assessment is None and res.error.startswith("schema")


def test_grounding_counts_failures(rec):
    a = Assessment.model_validate({**GOOD, "account_id": "T-1", "drivers": [
        {"driver": "usage_decline", "explanation": "x",
         "evidence": [{"field": "vehicles_active_now", "value": 20},
                      {"field": "weekly_active_users_now", "value": 9}]}]})
    verified, total, failures = grounding(a, rec)
    assert (verified, total) == (1, 2) and "weekly_active_users_now" in failures[0]


class RefusingClient(FakeClient):
    """Refuses a forced tool choice once, like an API that won't combine it with thinking."""

    def create(self, **kwargs):
        if kwargs["tool_choice"]["type"] == "tool":
            err = Exception("tool_choice forces tool use, which is not compatible with thinking")
            err.status_code = 400
            raise err
        self.calls += 1
        block = SimpleNamespace(type="tool_use", input=self.tool_input)
        return SimpleNamespace(content=[block], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=900, output_tokens=150))


def test_scorer_falls_back_when_forced_tool_refused(rec, tmp_path):
    import json
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=RefusingClient(GOOD), cache_dir=tmp_path / "c",
                     budget=_budget(tmp_path))
    assert s.score(rec).assessment.at_risk
    cached = json.loads(next((tmp_path / "c").rglob("*.json")).read_text())
    assert cached["call_settings"] == ["tool_choice=any"]
