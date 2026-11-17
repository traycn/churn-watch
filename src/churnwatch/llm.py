"""Score one account with Claude, through a forced tool call that returns an Assessment.

Responses are cached on disk, keyed by everything that affects the answer (model,
prompt text, schema, record, settings). Re-running an unchanged eval costs nothing,
and `replay=True` runs entirely from the cache with no API key, which is how the
tests and a fresh clone reproduce the published numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .budget import Budget, worst_case_cost
from .schema import Assessment, tool_schema

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = ROOT / "prompts"
CACHE_DIR = ROOT / "evals" / "cache"
TOOL_NAME = "record_assessment"


class CacheMiss(RuntimeError):
    pass


@dataclass
class Result:
    assessment: Assessment | None
    error: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cached: bool


def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text()


def model_input(record: dict) -> str:
    return json.dumps(record, indent=1, default=str)


def cache_key(model: str, prompt: str, record: dict, max_tokens: int, temperature: float | None) -> str:
    blob = json.dumps(
        {"model": model, "prompt": prompt, "schema": tool_schema(), "record": record,
         "max_tokens": max_tokens, "temperature": temperature},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class ClaudeScorer:
    def __init__(self, model: str, prompt_version: str, *, max_tokens: int = 4000,
                 temperature: float | None = 0, replay: bool = False, client=None,
                 cache_dir: Path = CACHE_DIR, budget: Budget | None = None):
        self.model = model
        self.prompt_version = prompt_version
        self.prompt = load_prompt(prompt_version)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.replay = replay
        self.cache_dir = cache_dir / model
        self._client = client
        self.budget = budget
        if not replay and budget is None:
            raise ValueError("Live scoring needs a Budget so spend stays under the cap.")

    def _cache_path(self, record: dict) -> Path:
        key = cache_key(self.model, self.prompt, record, self.max_tokens, self.temperature)
        return self.cache_dir / f"{key}.json"

    def is_cached(self, record: dict) -> bool:
        return self._cache_path(record).exists()

    def worst_case(self, record: dict) -> float:
        return worst_case_cost(self.budget.prices, self.model, self.prompt, model_input(record),
                               tool_schema(), self.max_tokens)

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so replay mode needs no API key

            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set. Use --replay to run from the cache.")
            self._client = anthropic.Anthropic(max_retries=2, timeout=120)
        return self._client

    def _call(self, record: dict) -> dict:
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.prompt,
            tools=[{"name": TOOL_NAME, "description": "Record the churn-risk assessment for this account.",
                    "input_schema": tool_schema()}],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": model_input(record)}],
        )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        # Newer models think by default, and some API versions refuse a forced tool
        # choice or a temperature alongside thinking. Fall back one setting at a time
        # and record what was actually used, so results stay explainable.
        notes: list[str] = []
        for _ in range(3):
            try:
                t0 = time.perf_counter()
                resp = self.client.messages.create(**kwargs)
                break
            except Exception as e:  # anthropic.BadRequestError, kept generic for the fake client
                msg = str(e).lower()
                if getattr(e, "status_code", None) != 400:
                    raise
                if "temperature" in msg and "temperature" in kwargs:
                    kwargs.pop("temperature")
                    notes.append("temperature dropped")
                elif "tool_choice" in msg and kwargs["tool_choice"].get("type") == "tool":
                    kwargs["tool_choice"] = {"type": "any"}
                    notes.append("tool_choice=any")
                else:
                    raise
        else:
            raise RuntimeError(f"API kept refusing the request after: {notes}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        tool_input = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        return {
            "tool_input": tool_input,
            "stop_reason": resp.stop_reason,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "latency_ms": latency_ms,
            "call_settings": notes,
        }

    def score(self, record: dict) -> Result:
        path = self._cache_path(record)
        cached = path.exists()
        if cached:
            raw = json.loads(path.read_text())
        elif self.replay:
            raise CacheMiss(f"No cached response for {record['account_id']} with {self.model}/{self.prompt_version}")
        else:
            # Each attempt in _call is at most one billed request; reserve the worst case
            # for it before sending and settle to the real cost after.
            reserved = self.worst_case(record)
            self.budget.reserve(reserved)
            try:
                raw = self._call(record)
            except Exception as e:
                if getattr(e, "status_code", None) is not None and 400 <= e.status_code < 500:
                    self.budget.release(reserved)  # rejected requests are not billed
                else:
                    # Unknown whether it was billed (timeout, dropped connection): count the worst case.
                    self.budget.settle(reserved, model=self.model, prompt=self.prompt_version,
                                       account_id=record["account_id"] + " (failed, counted at worst case)",
                                       input_tokens=0, output_tokens=0, cost_usd=reserved)
                raise
            self.budget.settle(reserved, model=self.model, prompt=self.prompt_version,
                               account_id=record["account_id"], input_tokens=raw["input_tokens"],
                               output_tokens=raw["output_tokens"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"account_id": record["account_id"], "prompt_version": self.prompt_version,
                                        **raw}, indent=1))
        assessment, error = None, None
        if raw.get("tool_input") is None:
            error = f"no tool call (stop_reason={raw.get('stop_reason')})"
        else:
            try:
                assessment = Assessment.model_validate({**raw["tool_input"], "account_id": record["account_id"]})
            except ValidationError as e:
                error = f"schema: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}"
        return Result(assessment, error, raw["input_tokens"], raw["output_tokens"], raw["latency_ms"], cached)
