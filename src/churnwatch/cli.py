"""Command line: `churnwatch score`, `churnwatch eval`, `churnwatch readme`."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import baseline
from .accounts import load_accounts
from .grounding import grounding
from .llm import ClaudeScorer, Result


def cmd_score(args: argparse.Namespace) -> int:
    records = load_accounts(args.csv)[: args.limit or None]
    if args.model == "baseline":
        score = lambda rec: Result(baseline.assess(rec), None, 0, 0, 0, True)  # noqa: E731
        prompt = None
    else:
        scorer = ClaudeScorer(args.model, args.prompt, replay=args.replay)
        score, prompt = scorer.score, args.prompt

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(score, records))

    scored = []
    for rec, res in zip(records, results):
        bad: set[str] = set()
        if res.assessment:
            _, _, failures = grounding(res.assessment, rec)
            bad = {f.split(" (record has")[0].replace("'", "") for f in failures}
        scored.append({"rec": rec, "a": res.assessment, "error": res.error, "bad": bad})

    from .report import write_outputs

    csv_path, html_path = write_outputs(scored, Path(args.out), source=Path(args.csv).name,
                                        model=args.model, prompt=prompt)
    flagged = [s for s in scored if s["a"] and s["a"].at_risk]
    flagged.sort(key=lambda s: -s["a"].risk_score)
    print(f"Scored {len(scored)} accounts; {len(flagged)} at risk.\n")
    for s in flagged[:10]:
        a = s["a"]
        print(f"  {a.risk_score:>3}  {s['rec']['account_name'][:28]:<28} {a.main_reason:<20} "
              f"{a.drivers[0].explanation if a.drivers else ''}")
    print(f"\nWrote {csv_path} and {html_path}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals import run

    run(replay=args.replay, models=args.model or None, prompts=args.prompt or None)
    return 0


def cmd_readme(_: argparse.Namespace) -> int:
    from .readme import update_readme

    update_readme()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="churnwatch", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="Score a CRM export and write a ranked CSV and HTML report.")
    s.add_argument("csv")
    s.add_argument("--model", default="claude-haiku-5-5", help="A Claude model id, or 'baseline'.")
    s.add_argument("--prompt", default="v2")
    s.add_argument("--out", default="out")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--replay", action="store_true", help="Use cached responses only; no API calls.")
    s.set_defaults(func=cmd_score)

    e = sub.add_parser("eval", help="Score the labeled test set for every model and prompt in evals/config.toml.")
    e.add_argument("--replay", action="store_true", help="Use cached responses only; no API calls.")
    e.add_argument("--model", action="append", help="Limit to this model (repeatable).")
    e.add_argument("--prompt", action="append", help="Limit to this prompt version (repeatable).")
    e.set_defaults(func=cmd_eval)

    r = sub.add_parser("readme", help="Regenerate the README results table from evals/results.csv.")
    r.set_defaults(func=cmd_readme)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
