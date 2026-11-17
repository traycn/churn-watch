"""Spending guardrail: no API call is made unless its worst-case cost fits under the cap.

How it works:
- Every paid call is logged with its actual cost in evals/spend.csv, which is committed,
  so the running total survives across machines and CI runs.
- Before a run starts, `preflight` adds up the worst case for every call the run will
  make (cached responses are free and not counted). If spent + worst case > cap, the
  run is refused before any call is made.
- During the run, each call reserves its worst case before it is sent and settles to
  its actual cost afterwards. Concurrent calls can therefore never overshoot the cap,
  even if the pre-flight estimate were wrong.

Worst case per call = an upper bound on input tokens (characters / 3, above any real
tokenizer ratio for English and JSON) at the input price, plus max_tokens at the output
price. Real calls cost less, often far less.

This guards spend made through this code. The only hard guarantee against any use of
the key is a spend limit on the Anthropic side; see the README.
"""

from __future__ import annotations

import csv
import json
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LEDGER_FIELDS = ["at", "model", "prompt", "account_id", "input_tokens", "output_tokens", "cost_usd"]


class BudgetExceeded(RuntimeError):
    pass


def price_for(prices: dict, model: str) -> dict:
    if model not in prices:
        raise BudgetExceeded(
            f"No price configured for {model!r} in evals/config.toml, so its cost can't be bounded. "
            "Add its [prices] entry before using it.")
    return prices[model]


def call_cost(prices: dict, model: str, input_tokens: int, output_tokens: int) -> float:
    p = price_for(prices, model)
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1e6


def worst_case_cost(prices: dict, model: str, prompt: str, user_text: str, tool_schema: dict,
                    max_tokens: int) -> float:
    chars = len(prompt) + len(user_text) + len(json.dumps(tool_schema)) + 600  # + tool/system overhead
    return call_cost(prices, model, math.ceil(chars / 3), max_tokens)


@dataclass
class Budget:
    cap_usd: float
    prices: dict
    ledger: Path

    def __post_init__(self):
        self._lock = threading.Lock()
        self._reserved = 0.0

    def spent(self) -> float:
        if not self.ledger.exists():
            return 0.0
        with self.ledger.open() as f:
            return round(sum(float(r["cost_usd"]) for r in csv.DictReader(f)), 6)

    def remaining(self) -> float:
        return round(self.cap_usd - self.spent(), 6)

    def preflight(self, worst_case_usd: float, what: str) -> None:
        spent = self.spent()
        if spent + worst_case_usd > self.cap_usd:
            raise BudgetExceeded(
                f"Refusing {what}: worst case ${worst_case_usd:.2f} + already spent ${spent:.2f} "
                f"> cap ${self.cap_usd:.2f}. Nothing was sent. Reduce the run (fewer models/prompts, "
                f"--limit) or raise budget.cap_usd in evals/config.toml.")

    def reserve(self, amount: float) -> None:
        with self._lock:
            if self.spent() + self._reserved + amount > self.cap_usd:
                raise BudgetExceeded(
                    f"Budget cap ${self.cap_usd:.2f} reached (spent ${self.spent():.2f}, "
                    f"${self._reserved:.2f} in flight). Call not sent.")
            self._reserved += amount

    def settle(self, reserved: float, *, model: str, prompt: str, account_id: str,
               input_tokens: int, output_tokens: int, cost_usd: float | None = None) -> float:
        cost = cost_usd if cost_usd is not None else call_cost(self.prices, model, input_tokens, output_tokens)
        with self._lock:
            self._reserved -= reserved
            new = not self.ledger.exists()
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
                if new:
                    w.writeheader()
                w.writerow({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "model": model, "prompt": prompt, "account_id": account_id,
                            "input_tokens": input_tokens, "output_tokens": output_tokens,
                            "cost_usd": f"{cost:.6f}"})
        return cost

    def release(self, reserved: float) -> None:
        """A call that failed before being billed gives its reservation back."""
        with self._lock:
            self._reserved -= reserved
