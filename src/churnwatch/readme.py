"""Generate pack READMEs and rubrics, and keep every results table current."""

from __future__ import annotations

import csv
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .llm import ROOT
from .pack import Pack, all_packs

README = ROOT / "README.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
PSTART, PEND = "<!-- PACKS:START -->", "<!-- PACKS:END -->"
_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), keep_trailing_newline=True)


def _pct(v: str) -> str:
    return f"{float(v) * 100:.0f}%" if v not in ("", None) else "–"


def _latest(pack: Pack) -> tuple[str, dict[tuple[str, str], dict]]:
    path = pack.evals_dir / "results.csv"
    rows = list(csv.DictReader(path.open())) if path.exists() else []
    label_set = "reviewed" if any(r["label_set"] == "reviewed" for r in rows) else "draft"
    latest = {}
    for r in rows:
        if r["label_set"] == label_set:
            latest[(r["model"], r["prompt"])] = r
    return label_set, latest


def results_table(pack: Pack) -> str:
    label_set, latest = _latest(pack)
    if not latest:
        return "_No evaluation runs yet. Run `churnwatch eval --pack " + pack.slug + "`._"
    order = sorted(latest, key=lambda k: (k[0] != "baseline", k[0], k[1]))
    out = [
        "| Model | Prompt | Precision | Recall | F1 | Ambiguous cases right | Main reason right | "
        "Evidence verified | Errors | Cost per 1,000 accounts | Median latency | Run |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for k in order:
        r = latest[k]
        base = r["model"] == "baseline"
        out.append(
            f"| {'Rules baseline' if base else '`' + r['model'] + '`'} | {'–' if base else r['prompt']} | "
            f"{_pct(r['precision'])} | {_pct(r['recall'])} | {_pct(r['f1'])} | {_pct(r['ambiguous_accuracy'])} | "
            f"{_pct(r['reason_accuracy'])} | {r['grounded_evidence_pct']}% | {r['errors']} | "
            f"{'–' if base else '$' + r['cost_per_1k_accounts_usd']} | "
            f"{'–' if base else r['p50_latency_s'] + ' s'} | {r['run_at'][:10]} `{r['git_sha']}` |")
    n = sum(1 for _ in (pack.evals_dir / "results.csv").open()) - 1
    out.append(f"\nLabels: **{label_set}** ("
               f"{'reviewed by hand against the rubric' if label_set == 'reviewed' else 'generator drafts, not yet reviewed'}). "
               f"{n} runs logged in [`evals/results.csv`](evals/results.csv); per-account errors in "
               f"[`evals/misses.md`](evals/misses.md).")
    return "\n".join(out)


def _replace_block(path: Path, start: str, end: str, body: str) -> None:
    if not path.exists():
        return
    text = path.read_text()
    if start not in text:
        return
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    path.write_text(f"{head}{start}\n{body}\n{end}{tail}")


def write_pack_docs(pack: Pack, n: int, ambiguous: int) -> None:
    """Rubric is always regenerated from pack.toml; the README only if it doesn't exist yet."""
    seasonal = ", ".join(s["name"] for s in pack.raw["segments"] if s.get("seasonal"))
    (pack.path / "rubric.md").write_text(_env.get_template("rubric.md.j2").render(
        pack=pack, n=n, reasons=list(pack.reasons.items()), seasonal=seasonal))
    if not pack.readme.exists():
        pack.readme.write_text(_env.get_template("pack_readme.md.j2").render(
            pack=pack, n=n, ambiguous=ambiguous, product=" ".join(pack.meta["product"].split()),
            fields=list(pack.descriptions.items()), units_h=pack.words["units_h"]))


def packs_table(packs: list[Pack]) -> str:
    if not packs:
        return "_No packs yet. Create one with `churnwatch new-pack <slug>`._"
    out = ["| Pack | Built for | Best F1 (LLM) | Rules baseline F1 | Ambiguous cases right (best LLM) | Labels |",
           "| --- | --- | ---: | ---: | ---: | --- |"]
    for p in packs:
        label_set, latest = _latest(p)
        llm = [r for (m, _), r in latest.items() if m != "baseline"]
        best = max(llm, key=lambda r: float(r["f1"]), default=None)
        base = latest.get(("baseline", "-"))
        target = ", ".join(x for x in (p.meta.get("target_role", ""), p.meta.get("target_company", "")) if x) or "–"
        out.append(f"| [{p.name}](packs/{p.slug}/) | {target} | "
                   f"{_pct(best['f1']) + ' (' + best['model'].replace('claude-', '') + ' ' + best['prompt'] + ')' if best else '–'} | "
                   f"{_pct(base['f1']) if base else '–'} | {_pct(best['ambiguous_accuracy']) if best else '–'} | "
                   f"{label_set if latest else '–'} |")
    return "\n".join(out)


def update_all() -> None:
    packs = all_packs()
    for p in packs:
        _replace_block(p.readme, START, END, results_table(p))
    _replace_block(README, PSTART, PEND, packs_table(packs))
