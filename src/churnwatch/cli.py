"""Command line: `churnwatch score`, `churnwatch eval`, `churnwatch readme`."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import baseline
from .accounts import load_accounts
from .grounding import grounding
from .llm import Result


def cmd_score(args: argparse.Namespace) -> int:
    records = load_accounts(args.csv)[: args.limit or None]
    if args.model == "baseline":
        score = lambda rec: Result(baseline.assess(rec), None, 0, 0, 0, True)  # noqa: E731
        prompt = None
    else:
        from .evals import guarded_preflight, load_config, make_budget, make_scorer

        cfg = load_config()
        budget = None if args.replay else make_budget(cfg)
        scorer = make_scorer(args.model, args.prompt, cfg, args.replay, budget)
        if budget:
            todo = [r for r in records if not scorer.is_cached(r)]
            worst = sum(scorer.worst_case(r) for r in todo)
            covers = {f"demo:{args.model}/{args.prompt}"} if Path(args.csv).name == "accounts.csv" and not args.limit else set()
            guarded_preflight(cfg, budget, worst, covers, f"scoring {len(todo)} uncached accounts")
            print(f"Budget: spent ${budget.spent():.2f} of ${budget.cap_usd:.2f}; this run costs at most ${worst:.2f}.")
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


def cmd_budget(_: argparse.Namespace) -> int:
    from .evals import load_config, make_budget, required_worst

    cfg = load_config()
    b = make_budget(cfg)
    total, lines = required_worst(cfg, b)
    print(f"Cap:        ${b.cap_usd:.2f}")
    print(f"Spent:      ${b.spent():.2f}   (evals/spend.csv)")
    print(f"Remaining:  ${b.remaining():.2f}\n")
    print("Worst case to finish the required runs (uncached calls only; cached runs cost nothing):")
    for item, n, c in lines:
        print(f"  {item:<32} {n:>3} calls  ${c:.2f}")
    fits = total <= b.remaining()
    print(f"  {'total':<32}            ${total:.2f}  ->  {'fits' if fits else 'DOES NOT FIT'} "
          f"in the remaining ${b.remaining():.2f}")
    print(f"\nAvailable for extra experiments without endangering the required runs: "
          f"${max(0.0, b.remaining() - total):.2f}")
    return 0 if fits else 1


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

    b = sub.add_parser("budget", help="Show spend so far and the worst-case cost of finishing the project.")
    b.set_defaults(func=cmd_budget)

    r = sub.add_parser("readme", help="Regenerate the README results table from evals/results.csv.")
    r.set_defaults(func=cmd_readme)

    args = p.parse_args(argv)
    from .budget import BudgetExceeded

    try:
        return args.func(args)
    except BudgetExceeded as e:
        print(f"\nBUDGET GUARD: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
