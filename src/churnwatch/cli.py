"""churnwatch: flag at-risk accounts from a CRM export and explain why, per industry pack.

  churnwatch packs                     list packs
  churnwatch new-pack <slug>           start a pack from packs/_template
  churnwatch generate --pack <slug>    synthetic export, test set, draft labels, rubric, README
  churnwatch score --pack <slug>       score the pack's export (or any CSV) and write a report
  churnwatch eval [--pack <slug>]      score test sets for every configured model x prompt
  churnwatch budget                    spend so far and the worst case to finish
  churnwatch readme                    regenerate results tables
"""

from __future__ import annotations

import argparse
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .budget import BudgetExceeded
from .pack import PackError, all_packs, list_slugs, load_pack, packs_dir


def _packs(args) -> list:
    return [load_pack(s) for s in args.pack] if args.pack else all_packs()


def cmd_packs(_: argparse.Namespace) -> int:
    slugs = list_slugs()
    if not slugs:
        print("No packs yet. Start one with: churnwatch new-pack <slug>")
    for s in slugs:
        p = load_pack(s)
        target = ", ".join(x for x in (p.meta.get("target_role"), p.meta.get("target_company")) if x)
        state = "generated" if p.testset_csv.exists() else "not generated"
        labels = "reviewed labels" if p.labels_csv.exists() else "draft labels"
        print(f"{s:<28} {p.name}  [{target or 'no target'}; {state}; {labels}]")
    return 0


def cmd_new_pack(args: argparse.Namespace) -> int:
    dest = packs_dir() / args.slug
    if dest.exists():
        print(f"{dest} already exists.", file=sys.stderr)
        return 1
    dest.mkdir(parents=True)
    shutil.copy(packs_dir() / "_template" / "pack.toml", dest / "pack.toml")
    print(f"Created {dest}/pack.toml from the template. Edit it, then run:\n"
          f"  churnwatch generate --pack {args.slug}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    from .generate import generate
    from .readme import update_all, write_pack_docs

    pack = load_pack(args.pack)
    if pack.labels_csv.exists() and not args.force:
        print(f"{pack.labels_csv} exists (reviewed labels). Regenerating would invalidate them; "
              "pass --force to do it anyway.", file=sys.stderr)
        return 1
    stats = generate(pack)
    write_pack_docs(pack, stats["testset"], stats["ambiguous"])
    update_all()
    print(f"[{pack.slug}] {stats['export']} export accounts; {stats['testset']} test accounts "
          f"({stats['at_risk']} at risk, {stats['ambiguous']} ambiguous). "
          f"Draft labels to review: {pack.labels_draft_csv}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    from . import baseline
    from .accounts import load_accounts
    from .evals import guarded_preflight, load_config, make_budget, make_scorer, run_key
    from .grounding import grounding
    from .llm import Result
    from .report import write_outputs

    pack = load_pack(args.pack)
    csv_path = Path(args.csv) if args.csv else pack.export_csv
    records = load_accounts(csv_path, pack)[: args.limit or None]
    cfg = load_config()
    model, prompt = (args.model, args.prompt) if args.model else pack.demo_run(cfg["defaults"]["demo"])
    if model == "baseline":
        score = lambda rec: Result(baseline.assess(rec, pack), None, 0, 0, 0, True)  # noqa: E731
        prompt = None
    else:
        budget = None if args.replay else make_budget(cfg)
        scorer = make_scorer(pack, model, prompt, cfg, args.replay, budget)
        if budget:
            todo = [r for r in records if not scorer.is_cached(r)]
            worst = sum(scorer.worst_case(r) for r in todo)
            is_demo = csv_path.resolve() == pack.export_csv.resolve() and not args.limit
            covers = {run_key(pack, "demo", model, prompt)} if is_demo else set()
            guarded_preflight(cfg, budget, worst, covers, f"scoring {len(todo)} uncached accounts")
            print(f"Budget: spent ${budget.spent():.2f} of ${budget.cap_usd:.2f}; this run costs at most ${worst:.2f}.")
        score = scorer.score

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(score, records))

    notes_col = pack.col("csm_notes")
    scored = []
    for rec, res in zip(records, results):
        bad: set[str] = set()
        if res.assessment:
            _, _, failures = grounding(res.assessment, rec, notes_col)
            bad = {f.split(" (record has")[0].replace("'", "") for f in failures}
        scored.append({"rec": rec, "a": res.assessment, "error": res.error, "bad": bad})

    out = Path(args.out) if args.out else Path("out") / pack.slug
    csv_out, html_out = write_outputs(scored, out, pack=pack, source=csv_path.name, model=model, prompt=prompt)
    flagged = sorted((s for s in scored if s["a"] and s["a"].at_risk), key=lambda s: -s["a"].risk_score)
    print(f"[{pack.slug}] scored {len(scored)} accounts with {model}{'/' + prompt if prompt else ''}; "
          f"{len(flagged)} at risk.\n")
    for s in flagged[:10]:
        a = s["a"]
        print(f"  {a.risk_score:>3}  {s['name'][:28]:<28} {a.main_reason:<20} "
              f"{a.drivers[0].explanation if a.drivers else ''}")
    print(f"\nWrote {csv_out} and {html_out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals import run

    run(packs=_packs(args), replay=args.replay, models=args.model or None, prompts=args.prompt or None)
    return 0


def cmd_budget(_: argparse.Namespace) -> int:
    from .evals import load_config, make_budget, required_worst

    cfg = load_config()
    b = make_budget(cfg)
    total, lines = required_worst(cfg, b)
    print(f"Cap:        ${b.cap_usd:.2f}")
    print(f"Spent:      ${b.spent():.2f}   (evals/spend.csv)")
    print(f"Remaining:  ${b.remaining():.2f}\n")
    print("Worst case to finish every pack's required runs (uncached calls only; cached runs are free):")
    if not lines:
        print("  (no packs generated yet)")
    for item, n, c in lines:
        print(f"  {item:<52} {n:>3} calls  ${c:.2f}")
    fits = total <= b.remaining()
    print(f"  {'total':<52}            ${total:.2f}  ->  {'fits' if fits else 'DOES NOT FIT'} "
          f"in the remaining ${b.remaining():.2f}")
    print(f"\nAvailable for extra experiments without endangering the required runs: "
          f"${max(0.0, b.remaining() - total):.2f}")
    return 0 if fits else 1


def cmd_readme(_: argparse.Namespace) -> int:
    from .readme import update_all

    update_all()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="churnwatch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("packs", help="List industry packs.").set_defaults(func=cmd_packs)

    n = sub.add_parser("new-pack", help="Start a new pack from packs/_template.")
    n.add_argument("slug")
    n.set_defaults(func=cmd_new_pack)

    g = sub.add_parser("generate", help="Generate a pack's synthetic data, draft labels, rubric and README.")
    g.add_argument("--pack", required=True)
    g.add_argument("--force", action="store_true", help="Regenerate even if reviewed labels exist.")
    g.set_defaults(func=cmd_generate)

    s = sub.add_parser("score", help="Score a pack's export (or --csv) and write a ranked CSV and HTML report.")
    s.add_argument("--pack", required=True)
    s.add_argument("--csv", help="Defaults to the pack's data/accounts.csv.")
    s.add_argument("--model", help="A Claude model id, or 'baseline'. Defaults to the pack's demo run.")
    s.add_argument("--prompt", default="v2")
    s.add_argument("--out", help="Defaults to out/<pack>.")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--replay", action="store_true", help="Use cached responses only; no API calls.")
    s.set_defaults(func=cmd_score)

    e = sub.add_parser("eval", help="Score test sets for every configured model x prompt.")
    e.add_argument("--pack", action="append", help="Limit to this pack (repeatable). Default: all packs.")
    e.add_argument("--replay", action="store_true", help="Use cached responses only; no API calls.")
    e.add_argument("--model", action="append", help="Limit to this model (repeatable).")
    e.add_argument("--prompt", action="append", help="Limit to this prompt version (repeatable).")
    e.set_defaults(func=cmd_eval)

    sub.add_parser("budget", help="Spend so far and worst-case cost to finish.").set_defaults(func=cmd_budget)
    sub.add_parser("readme", help="Regenerate results tables.").set_defaults(func=cmd_readme)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except BudgetExceeded as e:
        print(f"\nBUDGET GUARD: {e}", file=sys.stderr)
        return 2
    except PackError as e:
        print(f"\nPACK ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
