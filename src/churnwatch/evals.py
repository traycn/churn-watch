"""Score every (model, prompt) pair on the labeled test set and log the results.

Outputs:
  evals/results.csv     one row per pair per run whose numbers changed (the history)
  evals/runs/*.json     per-account detail for the latest run of each pair
  evals/misses.md       every account each pair got wrong, for error analysis
  README.md             the results table between the RESULTS markers
"""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import baseline
from .accounts import load_accounts, load_labels
from .grounding import grounding
from .llm import ROOT, ClaudeScorer, Result

EVALS = ROOT / "evals"
RESULTS_CSV = EVALS / "results.csv"
RESULT_FIELDS = [
    "run_at", "git_sha", "label_set", "model", "prompt", "n",
    "precision", "recall", "f1", "accuracy", "ambiguous_accuracy", "reason_accuracy",
    "grounded_evidence_pct", "fully_grounded_pct", "errors",
    "cost_per_1k_accounts_usd", "p50_latency_s",
]
METRIC_FIELDS = RESULT_FIELDS[6:]


@dataclass
class Row:
    account_id: str
    label_at_risk: bool
    label_reason: str
    ambiguous: bool
    result: Result
    verified: int = 0
    evidence: int = 0
    grounding_failures: list[str] = field(default_factory=list)


def load_config(path: Path = EVALS / "config.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_labels(cfg: dict) -> tuple[Path, str]:
    """Use reviewed labels if they exist, else fall back to the generator's drafts."""
    reviewed = ROOT / cfg["labels"]
    if reviewed.exists():
        return reviewed, "reviewed"
    return ROOT / "data/testset/labels_draft.csv", "draft"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def run_pair(model: str, prompt: str, records: list[dict], labels: dict, cfg: dict, replay: bool) -> list[Row]:
    if model == "baseline":
        def score(rec):
            return Result(baseline.assess(rec), None, 0, 0, 0, True)
    else:
        scorer = ClaudeScorer(model, prompt, max_tokens=cfg.get("max_tokens", 4000),
                              temperature=cfg.get("temperature"), replay=replay)
        score = scorer.score

    with ThreadPoolExecutor(max_workers=cfg.get("concurrency", 8)) as pool:
        results = list(pool.map(score, records))

    rows = []
    for rec, res in zip(records, results):
        lab = labels[rec["account_id"]]
        row = Row(rec["account_id"], lab["at_risk"] == "yes", lab["main_reason"], lab["ambiguous"] == "yes", res)
        if res.assessment:
            row.verified, row.evidence, row.grounding_failures = grounding(res.assessment, rec)
        rows.append(row)
    return rows


def metrics(rows: list[Row], model: str, cfg: dict) -> dict:
    ok = [r for r in rows if r.result.assessment]
    pred = {r.account_id: r.result.assessment.at_risk for r in ok}
    # An account with no valid answer counts as "not flagged": a CSM would never see it.
    tp = sum(1 for r in rows if r.label_at_risk and pred.get(r.account_id, False))
    fp = sum(1 for r in rows if not r.label_at_risk and pred.get(r.account_id, False))
    fn = sum(1 for r in rows if r.label_at_risk and not pred.get(r.account_id, False))
    correct = [r for r in rows if pred.get(r.account_id, False) == r.label_at_risk]
    amb = [r for r in rows if r.ambiguous]
    caught = [r for r in ok if r.label_at_risk and r.result.assessment.at_risk]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    evidence = sum(r.evidence for r in ok)
    price = cfg.get("prices", {}).get(model, {"input": 0, "output": 0})
    cost = sum(r.result.input_tokens * price["input"] + r.result.output_tokens * price["output"]
               for r in rows) / 1e6
    latencies = [r.result.latency_ms for r in rows if r.result.latency_ms]
    return {
        "n": len(rows),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "accuracy": round(len(correct) / len(rows), 3),
        "ambiguous_accuracy": round(sum(1 for r in amb if r in correct) / len(amb), 3) if amb else "",
        "reason_accuracy": round(sum(1 for r in caught if r.result.assessment.main_reason == r.label_reason)
                                 / len(caught), 3) if caught else 0.0,
        "grounded_evidence_pct": round(100 * sum(r.verified for r in ok) / evidence, 1) if evidence else 0.0,
        "fully_grounded_pct": round(100 * sum(1 for r in ok if r.verified == r.evidence) / len(rows), 1),
        "errors": len(rows) - len(ok),
        "cost_per_1k_accounts_usd": round(cost / len(rows) * 1000, 2),
        "p50_latency_s": round(statistics.median(latencies) / 1000, 1) if latencies else 0.0,
    }


def _latest_rows() -> list[dict]:
    if not RESULTS_CSV.exists():
        return []
    with RESULTS_CSV.open() as f:
        return list(csv.DictReader(f))


def _same(a: dict, b: dict) -> bool:
    return all(str(a[k]) == str(b[k]) for k in METRIC_FIELDS)


def write_run_detail(model: str, prompt: str, rows: list[Row], label_set: str) -> None:
    out = EVALS / "runs" / f"{model}__{prompt}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    detail = []
    for r in rows:
        a = r.result.assessment
        detail.append({
            "account_id": r.account_id,
            "label": {"at_risk": r.label_at_risk, "main_reason": r.label_reason, "ambiguous": r.ambiguous},
            "prediction": a.model_dump() if a else None,
            "error": r.result.error,
            "grounding": {"verified": r.verified, "total": r.evidence, "failures": r.grounding_failures},
        })
    out.write_text(json.dumps({"model": model, "prompt": prompt, "label_set": label_set, "accounts": detail},
                              indent=1))


def misses_markdown(all_rows: dict[tuple[str, str], list[Row]], records: dict[str, dict]) -> str:
    lines = ["# Misses by model and prompt", "",
             "Every test account each configuration got wrong. Generated by `churnwatch eval`.", ""]
    for (model, prompt), rows in all_rows.items():
        name = model if model == "baseline" else f"{model} / {prompt}"
        wrong = [r for r in rows if not r.result.assessment or r.result.assessment.at_risk != r.label_at_risk
                 or (r.label_at_risk and r.result.assessment.main_reason != r.label_reason)]
        lines += [f"## {name} ({len(wrong)} of {len(rows)})", ""]
        if not wrong:
            lines += ["None.", ""]
            continue
        lines += ["| Account | Label | Predicted | Why it said so | CSM notes |", "| --- | --- | --- | --- | --- |"]
        for r in wrong:
            a = r.result.assessment
            label = f"{'at risk' if r.label_at_risk else 'ok'} ({r.label_reason})"
            if a:
                pred = f"{'at risk' if a.at_risk else 'ok'} ({a.main_reason}, {a.risk_score})"
                why = a.drivers[0].explanation if a.drivers else ""
            else:
                pred, why = "error", r.result.error or ""
            notes = (records[r.account_id]["csm_notes"] or "(none)").replace("|", "/")
            lines.append(f"| {r.account_id}{' *' if r.ambiguous else ''} | {label} | {pred} | "
                         f"{why.replace('|', '/')} | {notes} |")
        lines += ["", "\\* ambiguous by design", ""]
    return "\n".join(lines)


def run(replay: bool = False, models: list[str] | None = None, prompts: list[str] | None = None) -> list[dict]:
    cfg = load_config()
    label_path, label_set = resolve_labels(cfg)
    records = load_accounts(ROOT / cfg["testset"])
    labels = load_labels(label_path)
    by_id = {r["account_id"]: r for r in records}

    history = _latest_rows()
    new_rows, all_rows = [], {}
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = _git_sha()

    pairs = [(m, p) for m in (models or cfg["models"]) for p in (prompts or cfg["prompts"])]
    seen_baseline = False
    for model, prompt in pairs:
        if model == "baseline":
            if seen_baseline:
                continue
            seen_baseline, prompt = True, "-"
        rows = run_pair(model, prompt, records, labels, cfg, replay)
        all_rows[(model, prompt)] = rows
        m = metrics(rows, model, cfg)
        row = {"run_at": run_at, "git_sha": sha, "label_set": label_set, "model": model, "prompt": prompt, **m}
        prev = [h for h in history if h["model"] == model and h["prompt"] == prompt and h["label_set"] == label_set]
        if not prev or not _same(prev[-1], row):
            new_rows.append(row)
        write_run_detail(model, prompt, rows, label_set)
        print(f"{model:>18} {prompt:>3}  P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f} "
              f"amb={m['ambiguous_accuracy']} reason={m['reason_accuracy']:.2f} "
              f"grounded={m['grounded_evidence_pct']}% errors={m['errors']} "
              f"${m['cost_per_1k_accounts_usd']}/1k")

    if new_rows:
        write_header = not RESULTS_CSV.exists()
        with RESULTS_CSV.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new_rows)
    (EVALS / "misses.md").write_text(misses_markdown(all_rows, by_id))
    from .readme import update_readme

    update_readme()
    print(f"{len(new_rows)} new result row(s); label set: {label_set}")
    return new_rows
