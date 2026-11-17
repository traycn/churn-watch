"""Score every configured (model, prompt) run on each pack's labeled test set.

Per pack (packs/<slug>/evals/):
  results.csv    one row per run whose numbers changed (the history)
  runs/*.json    per-account detail for the latest run of each (model, prompt)
  misses.md      every account each run got wrong, for error analysis
  ../README.md   the results table between the RESULTS markers

Shared (evals/):
  config.toml    default runs, prices, budget cap
  spend.csv      every paid API call and its cost, across all packs
  cache/         cached model responses
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
from .budget import Budget
from .grounding import grounding
from .llm import ROOT, CacheMiss, ClaudeScorer, Result
from .pack import Pack, all_packs

EVALS = ROOT / "evals"
LEDGER = EVALS / "spend.csv"
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


def make_budget(cfg: dict, ledger: Path = LEDGER) -> Budget:
    return Budget(cap_usd=float(cfg["budget"]["cap_usd"]), prices=cfg.get("prices", {}), ledger=ledger)


def make_scorer(pack: Pack, model: str, prompt: str, cfg: dict, replay: bool,
                budget: Budget | None) -> ClaudeScorer:
    return ClaudeScorer(model, prompt, pack, max_tokens=cfg.get("max_tokens", 4000),
                        replay=replay, budget=budget)


def pack_runs(pack: Pack, cfg: dict, models=None, prompts=None) -> list[tuple[str, str]]:
    """The baseline first, then the pack's (model, prompt) runs, optionally filtered."""
    runs = [("baseline", "-")]
    for m, p in pack.runs(cfg["defaults"]["runs"]):
        if (not models or m in models) and (not prompts or p in prompts):
            runs.append((m, p))
    if models and "baseline" not in models:
        runs = runs[1:]
    return runs


def run_key(pack: Pack, kind: str, model: str, prompt: str) -> str:
    return f"{pack.slug}:{kind}:{model}/{prompt}"


def required_items(cfg: dict, packs: list[Pack]) -> list[tuple[Pack, str, str, str, Path]]:
    """Every run the acceptance criteria need: each pack's eval runs plus its demo."""
    items = []
    for pack in packs:
        for m, p in pack.runs(cfg["defaults"]["runs"]):
            items.append((pack, "eval", m, p, pack.testset_csv))
        m, p = pack.demo_run(cfg["defaults"]["demo"])
        items.append((pack, "demo", m, p, pack.export_csv))
    return items


def worst_case(pack: Pack, model: str, prompt: str, csv_path: Path, cfg: dict, budget: Budget,
               limit: int = 0) -> tuple[int, float]:
    if not csv_path.exists():
        return 0, 0.0
    s = make_scorer(pack, model, prompt, cfg, False, budget)
    records = load_accounts(csv_path, pack)[: limit or None]
    todo = [r for r in records if not s.is_cached(r)]
    return len(todo), sum(s.worst_case(r) for r in todo)


def required_worst(cfg: dict, budget: Budget, packs: list[Pack] | None = None,
                   skip: set[str] = frozenset()) -> tuple[float, list[tuple[str, int, float]]]:
    total, lines = 0.0, []
    for pack, kind, m, p, path in required_items(cfg, packs if packs is not None else all_packs()):
        key = run_key(pack, kind, m, p)
        if key in skip:
            continue
        n, cost = worst_case(pack, m, p, path, cfg, budget)
        total += cost
        lines.append((key, n, cost))
    return total, lines


def guarded_preflight(cfg: dict, budget: Budget, this_run: float, covers: set[str], what: str,
                      packs: list[Pack] | None = None) -> None:
    """Refuse a run unless it AND every unfinished required run still fit under the cap."""
    reserve, _ = required_worst(cfg, budget, packs, skip=covers)
    budget.preflight(this_run + reserve, f"{what} (worst case ${this_run:.2f}, plus ${reserve:.2f} "
                                         "held for unfinished required runs)")


def resolve_labels(pack: Pack) -> tuple[Path, str]:
    """Use reviewed labels if they exist, else fall back to the generator's drafts."""
    if pack.labels_csv.exists():
        return pack.labels_csv, "reviewed"
    return pack.labels_draft_csv, "draft"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def run_pair(pack: Pack, model: str, prompt: str, records: list[dict], labels: dict, cfg: dict,
             replay: bool, budget: Budget | None = None) -> list[Row]:
    if model == "baseline":
        def score(rec):
            return Result(baseline.assess(rec, pack), None, 0, 0, 0, True)
    else:
        score = make_scorer(pack, model, prompt, cfg, replay, budget).score

    with ThreadPoolExecutor(max_workers=cfg.get("concurrency", 8)) as pool:
        results = list(pool.map(score, records))

    id_col, notes_col = pack.col("account_id"), pack.col("csm_notes")
    rows = []
    for rec, res in zip(records, results):
        lab = labels[rec[id_col]]
        row = Row(rec[id_col], lab["at_risk"] == "yes", lab["main_reason"], lab["ambiguous"] == "yes", res)
        if res.assessment:
            row.verified, row.evidence, row.grounding_failures = grounding(res.assessment, rec, notes_col)
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


def read_results(pack: Pack) -> list[dict]:
    path = pack.evals_dir / "results.csv"
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def _same(a: dict, b: dict) -> bool:
    return all(str(a[k]) == str(b[k]) for k in METRIC_FIELDS)


def write_run_detail(pack: Pack, model: str, prompt: str, rows: list[Row], label_set: str) -> None:
    out = pack.evals_dir / "runs" / f"{model}__{prompt}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    detail = [{
        "account_id": r.account_id,
        "label": {"at_risk": r.label_at_risk, "main_reason": r.label_reason, "ambiguous": r.ambiguous},
        "prediction": r.result.assessment.model_dump() if r.result.assessment else None,
        "error": r.result.error,
        "grounding": {"verified": r.verified, "total": r.evidence, "failures": r.grounding_failures},
    } for r in rows]
    out.write_text(json.dumps({"pack": pack.slug, "model": model, "prompt": prompt, "label_set": label_set,
                               "accounts": detail}, indent=1))


def misses_markdown(pack: Pack, all_rows: dict[tuple[str, str], list[Row]], records: dict[str, dict]) -> str:
    lines = [f"# Misses by model and prompt: {pack.name}", "",
             "Every test account each configuration got wrong. Generated by `churnwatch eval`.", ""]
    notes_col = pack.col("csm_notes")
    for (model, prompt), rows in all_rows.items():
        name = "Rules baseline" if model == "baseline" else f"{model} / {prompt}"
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
            notes = (records[r.account_id][notes_col] or "(none)").replace("|", "/")
            lines.append(f"| {r.account_id}{' *' if r.ambiguous else ''} | {label} | {pred} | "
                         f"{why.replace('|', '/')} | {notes} |")
        lines += ["", "\\* ambiguous by design", ""]
    return "\n".join(lines)


def run_pack(pack: Pack, cfg: dict, *, replay: bool, budget: Budget | None, models=None, prompts=None,
             run_at: str, sha: str) -> list[dict]:
    if not pack.testset_csv.exists():
        print(f"[{pack.slug}] no test set yet; run `churnwatch generate --pack {pack.slug}`")
        return []
    label_path, label_set = resolve_labels(pack)
    records = load_accounts(pack.testset_csv, pack)
    labels = load_labels(label_path)
    by_id = {r[pack.col("account_id")]: r for r in records}
    history = read_results(pack)
    new_rows, all_rows = [], {}
    for model, prompt in pack_runs(pack, cfg, models, prompts):
        try:
            rows = run_pair(pack, model, prompt, records, labels, cfg, replay, budget)
        except CacheMiss as e:
            print(f"[{pack.slug}] {model:>18} {prompt:>3}  skipped in replay: {e}")
            continue
        all_rows[(model, prompt)] = rows
        m = metrics(rows, model, cfg)
        row = {"run_at": run_at, "git_sha": sha, "label_set": label_set, "model": model, "prompt": prompt, **m}
        prev = [h for h in history if h["model"] == model and h["prompt"] == prompt and h["label_set"] == label_set]
        if not prev or not _same(prev[-1], row):
            new_rows.append(row)
        write_run_detail(pack, model, prompt, rows, label_set)
        print(f"[{pack.slug}] {model:>18} {prompt:>3}  P={m['precision']:.2f} R={m['recall']:.2f} "
              f"F1={m['f1']:.2f} amb={m['ambiguous_accuracy']} reason={m['reason_accuracy']:.2f} "
              f"grounded={m['grounded_evidence_pct']}% errors={m['errors']} ${m['cost_per_1k_accounts_usd']}/1k")

    results = pack.evals_dir / "results.csv"
    if new_rows:
        results.parent.mkdir(parents=True, exist_ok=True)
        write_header = not results.exists()
        with results.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new_rows)
    if all_rows:
        (pack.evals_dir / "misses.md").write_text(misses_markdown(pack, all_rows, by_id))
    print(f"[{pack.slug}] {len(new_rows)} new result row(s); labels: {label_set}")
    return new_rows


def run(packs: list[Pack] | None = None, replay: bool = False, models=None, prompts=None) -> list[dict]:
    cfg = load_config()
    packs = packs if packs is not None else all_packs()
    budget = None
    if not replay:
        budget = make_budget(cfg)
        worst, covers = 0.0, set()
        for pack in packs:
            for m, p in pack_runs(pack, cfg, models, prompts):
                if m == "baseline":
                    continue
                n, c = worst_case(pack, m, p, pack.testset_csv, cfg, budget)
                print(f"  {pack.slug} {m}/{p}: {n} uncached calls, worst case ${c:.2f}")
                worst += c
                covers.add(run_key(pack, "eval", m, p))
        guarded_preflight(cfg, budget, worst, covers, "this eval run")
        print(f"Budget: spent ${budget.spent():.2f} of ${budget.cap_usd:.2f}; this run costs at most ${worst:.2f}.")

    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = _git_sha()
    new = []
    for pack in packs:
        new += run_pack(pack, cfg, replay=replay, budget=budget, models=models, prompts=prompts,
                        run_at=run_at, sha=sha)
    from .readme import update_all

    update_all()
    if budget:
        print(f"Spent so far: ${budget.spent():.2f} of ${budget.cap_usd:.2f} cap.")
    return new
