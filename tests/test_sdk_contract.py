"""Exercise the real Anthropic SDK against a mock HTTP server, so a request the
installed SDK would refuse fails here instead of in a paid CI run."""

import inspect
import json

import httpx2 as httpx  # the HTTP library anthropic 2.x is built on
import pytest

from churnwatch.budget import Budget
from churnwatch.llm import ClaudeScorer, maybe_billed
from test_core import GOOD, PRICES, rec  # noqa: F401  (rec is a fixture)


def test_request_uses_only_arguments_the_sdk_accepts(pack, rec, tmp_path):
    from anthropic.resources.messages import Messages

    accepted = set(inspect.signature(Messages.create).parameters)
    b = Budget(25, PRICES, tmp_path / "spend.csv")
    s = ClaudeScorer("claude-haiku-5-5", "v2", pack, budget=b, cache_dir=tmp_path / "c")
    unknown = set(s.request_kwargs(rec)) - accepted
    assert not unknown, f"messages.create() would reject {unknown}"


def _sdk_client(handler):
    import anthropic

    return anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _message(tool_input):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "thinking", "thinking": "…", "signature": "sig"},
                    {"type": "tool_use", "id": "tu_1", "name": "record_assessment", "input": tool_input}],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 1800, "output_tokens": 420},
    }


def test_full_round_trip_through_the_sdk(pack, rec, tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_message(GOOD))

    b = Budget(25, PRICES, tmp_path / "spend.csv")
    s = ClaudeScorer("claude-haiku-5-5", "v2", pack, client=_sdk_client(handler), budget=b,
                     cache_dir=tmp_path / "c")
    res = s.score(rec)
    assert res.assessment.main_reason == "usage_decline"
    assert seen["tool_choice"] == {"type": "tool", "name": "record_assessment"}
    assert b.spent() == pytest.approx((1800 * 0.10 + 420 * 0.50) / 1e6)


def test_forced_tool_refused_by_api_falls_back(pack, rec, tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["tool_choice"]["type"])
        if body["tool_choice"]["type"] == "tool":
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "tool_choice of type 'tool' is not supported when thinking is enabled"}})
        return httpx.Response(200, json=_message(GOOD))

    b = Budget(25, PRICES, tmp_path / "spend.csv")
    s = ClaudeScorer("claude-haiku-5-5", "v2", pack, client=_sdk_client(handler), budget=b,
                     cache_dir=tmp_path / "c")
    assert s.score(rec).assessment.at_risk
    assert calls == ["tool", "any"]
    assert len(b.ledger.read_text().splitlines()) == 2  # header + one billed call


@pytest.mark.parametrize("status", [401, 429, 500, 529])
def test_api_error_statuses_are_not_billed(pack, rec, tmp_path, status):
    def handler(request):
        return httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})

    b = Budget(25, PRICES, tmp_path / "spend.csv")
    s = ClaudeScorer("claude-haiku-5-5", "v2", pack, client=_sdk_client(handler), budget=b,
                     cache_dir=tmp_path / "c")
    with pytest.raises(Exception):
        s.score(rec)
    assert b.spent() == 0


def test_errors_before_sending_are_not_billed():
    assert not maybe_billed(TypeError("unexpected keyword argument 'temperature'"))
    assert not maybe_billed(RuntimeError("ANTHROPIC_API_KEY is not set"))
    assert maybe_billed(TimeoutError())


def test_timeout_after_sending_is_counted(pack, rec, tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    b = Budget(25, PRICES, tmp_path / "spend.csv")
    s = ClaudeScorer("claude-haiku-5-5", "v2", pack, client=_sdk_client(handler), budget=b,
                     cache_dir=tmp_path / "c")
    with pytest.raises(Exception):
        s.score(rec)
    assert b.spent() == pytest.approx(s.worst_case(rec), rel=1e-3)
