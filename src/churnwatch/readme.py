"""Regenerate the results table in README.md from evals/results.csv."""

from __future__ import annotations

import csv

from .llm import ROOT

README = ROOT / "README.md"
RESULTS = ROOT / "evals" / "results.csv"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def _pct(v: str) -> str:
    return f"{float(v) * 100:.0f}%" if v not in ("", None) else "–"


def results_table() -> str:
    if not RESULTS.exists():
        return "_No evaluation runs yet. Run `make eval`._"
    with RESULTS.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "_No evaluation runs yet. Run `make eval`._"
    label_set = "reviewed" if any(r["label_set"] == "reviewed" for r in rows) else "draft"
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["label_set"] == label_set:
            latest[(r["model"], r["prompt"])] = r
    order = sorted(latest, key=lambda k: (k[0] != "baseline", k[0], k[1]))
    out = [
        "| Model | Prompt | Precision | Recall | F1 | Ambiguous cases right | Main reason right | "
        "Evidence verified | Errors | Cost per 1,000 accounts | Median latency | Run |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for k in order:
        r = latest[k]
        is_base = r["model"] == "baseline"
        out.append(
            f"| {'Rules baseline' if is_base else '`' + r['model'] + '`'} | {'–' if is_base else r['prompt']} | "
            f"{_pct(r['precision'])} | {_pct(r['recall'])} | {_pct(r['f1'])} | {_pct(r['ambiguous_accuracy'])} | "
            f"{_pct(r['reason_accuracy'])} | {r['grounded_evidence_pct']}% | {r['errors']} | "
            f"{'–' if is_base else '$' + r['cost_per_1k_accounts_usd']} | "
            f"{'–' if is_base else r['p50_latency_s'] + ' s'} | {r['run_at'][:10]} `{r['git_sha']}` |"
        )
    note = (f"\n\nLabels: **{label_set}** "
            f"({'reviewed by hand against the rubric' if label_set == 'reviewed' else 'generator drafts, not yet reviewed'}). "
            f"{len(rows)} runs logged in [`evals/results.csv`](evals/results.csv); "
            f"per-account errors in [`evals/misses.md`](evals/misses.md).")
    return "\n".join(out) + note


def update_readme() -> None:
    text = README.read_text()
    if START not in text:
        return
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    README.write_text(f"{head}{START}\n{results_table()}\n{END}{tail}")
