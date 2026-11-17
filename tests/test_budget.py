import csv
from concurrent.futures import ThreadPoolExecutor

import pytest

from churnwatch.accounts import load_accounts
from churnwatch.budget import Budget, BudgetExceeded, call_cost
from churnwatch.evals import guarded_preflight, load_config, make_scorer, required_worst
from churnwatch.llm import ClaudeScorer
from test_core import GOOD, PRICES, FakeClient, _budget, rec  # noqa: F401  (rec is a fixture)
from types import SimpleNamespace


def _spent_rows(path):
    return list(csv.DictReader(path.open())) if path.exists() else []


def test_cost_math():
    assert call_cost(PRICES, "claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_unknown_model_price_is_refused(tmp_path):
    with pytest.raises(BudgetExceeded, match="No price configured"):
        call_cost(PRICES, "some-new-model", 10, 10)


def test_preflight_refuses_over_cap(tmp_path):
    b = _budget(tmp_path, cap=1.0)
    b.preflight(0.99, "ok run")
    with pytest.raises(BudgetExceeded, match="Nothing was sent"):
        b.preflight(1.01, "big run")


def test_ledger_counts_toward_cap(tmp_path):
    b = _budget(tmp_path, cap=1.0)
    b.reserve(0.5)
    b.settle(0.5, model="claude-haiku-5-5", prompt="v2", account_id="A",
             input_tokens=1_000_000, output_tokens=1_000_000)  # $0.60
    assert b.spent() == pytest.approx(0.60)
    with pytest.raises(BudgetExceeded):
        b.preflight(0.50, "next run")


def test_live_scoring_requires_a_budget(tmp_path):
    with pytest.raises(ValueError, match="Budget"):
        ClaudeScorer("claude-haiku-5-5", "v2", client=FakeClient(GOOD), cache_dir=tmp_path)


def test_concurrent_calls_never_overshoot(tmp_path):
    """50 accounts in parallel against a cap that only fits a few worst cases."""
    recs = load_accounts("data/testset/accounts.csv")

    class Expensive(FakeClient):  # every call uses the full max_tokens of output
        def create(self, **kw):
            self.calls += 1
            block = SimpleNamespace(type="tool_use", input=self.tool_input)
            return SimpleNamespace(content=[block], stop_reason="tool_use",
                                   usage=SimpleNamespace(input_tokens=900, output_tokens=kw["max_tokens"]))

    client = Expensive(GOOD)
    b = _budget(tmp_path, cap=0.01)
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=client, cache_dir=tmp_path / "c", budget=b)
    per_call = s.worst_case(recs[0])
    sent, refused = 0, 0

    def go(r):
        try:
            s.score(r)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(go, recs))
    sent, refused = out.count(True), out.count(False)
    assert refused > 0 and sent == client.calls
    assert b.spent() <= b.cap_usd
    assert sent <= int(b.cap_usd / per_call)  # a call is only sent if its worst case fits


def test_failed_call_counted_at_worst_case(tmp_path, rec):
    class Boom(FakeClient):
        def create(self, **kw):
            raise TimeoutError("connection dropped")

    b = _budget(tmp_path)
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=Boom(GOOD), cache_dir=tmp_path / "c", budget=b)
    with pytest.raises(TimeoutError):
        s.score(rec)
    assert b.spent() == pytest.approx(s.worst_case(rec), rel=1e-3)


def test_rejected_request_is_free(tmp_path, rec):
    class Reject(FakeClient):
        def create(self, **kw):
            e = Exception("invalid model")
            e.status_code = 404
            raise e

    b = _budget(tmp_path)
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=Reject(GOOD), cache_dir=tmp_path / "c", budget=b)
    with pytest.raises(Exception):
        s.score(rec)
    assert b.spent() == 0 and b._reserved == pytest.approx(0)


def test_cached_calls_are_free(tmp_path, rec):
    b = _budget(tmp_path)
    s = ClaudeScorer("claude-haiku-5-5", "v2", client=FakeClient(GOOD), cache_dir=tmp_path / "c", budget=b)
    s.score(rec)
    first = b.spent()
    s.score(rec)
    assert b.spent() == first and len(_spent_rows(tmp_path / "spend.csv")) == 1


def test_required_runs_fit_in_the_cap():
    """The acceptance-criteria runs must fit in the configured cap with room to spare."""
    cfg = load_config()
    b = Budget(cap_usd=cfg["budget"]["cap_usd"], prices=cfg["prices"], ledger=__import__("pathlib").Path("/nonexistent"))
    total, lines = required_worst(cfg, b)
    assert len(lines) == len(cfg["budget"]["required"])
    assert total <= cfg["budget"]["cap_usd"] / 2


def test_experiment_cannot_eat_the_required_reserve(tmp_path):
    cfg = load_config()
    reserve, _ = required_worst(cfg, Budget(25, cfg["prices"], tmp_path / "x.csv"))
    b = Budget(cap_usd=reserve + 1.0, prices=cfg["prices"], ledger=tmp_path / "spend.csv")
    guarded_preflight(cfg, b, 0.5, set(), "small experiment")          # fits beside the reserve
    with pytest.raises(BudgetExceeded, match="held for unfinished required runs"):
        guarded_preflight(cfg, b, 1.5, set(), "big experiment")      # would eat into it
    guarded_preflight(cfg, b, reserve, {i for i in cfg["budget"]["required"]}, "the required runs")
