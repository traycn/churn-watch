# churn-watch

**An LLM agent that reads a CRM export, flags the accounts likely to churn, and explains why. It's built as one engine with an industry pack per market, and every pack is scored against a hand-labeled test set whenever a prompt, model or pack changes.**

A customer success manager (CSM) with 200 accounts gets a weekly CRM export and has time to call about ten of them. churn-watch ranks the book and returns, for each at-risk account, the main reason, the evidence behind it, and one next step.

The churn problem looks the same in most industries, but the details differ: what a customer pays for, what "active" means, and what a CSM writes in the notes. So churn-watch separates the two:
- **The engine** handles scoring, the evidence check, the evaluations, the budget cap and the report.
- **A pack** describes one industry in a single [`pack.toml`](packs/_template/pack.toml).

Adding an industry means writing a pack, not changing code.

> All data is synthetic, generated from each pack by the engine. No real customer data is used.

## Packs

<!-- PACKS:START -->
| Pack | Built for | Best F1 (LLM) | Rules baseline F1 | Ambiguous cases right (best LLM) | Labels |
| --- | --- | ---: | ---: | ---: | --- |
| [Pharma patient-access programs](packs/pharma-patient-access/) | Senior Solutions Consultant, Gifthealth | – | 69% | – | draft |
<!-- PACKS:END -->

Each pack folder has its own README with the product, the data dictionary, full results and every miss.

## How it works

```
packs/<industry>/pack.toml ──► churnwatch generate ──► synthetic CRM export + 50-account test set + draft labels
                                                          │
                                         person reviews labels against the pack's rubric
                                                          ▼
CRM export (CSV) ──► add derived fields (utilization %, 90-day change %)
                 ──► Claude: one call per account, forced tool call → Assessment JSON
                     (at_risk, risk_score, main_reason, ≤3 drivers with (field, value) evidence, next_step)
                 ──► grounding check: every cited value compared with the source row
                 ──► ranked CSV + one-page HTML report ("accounts to call this week")
```

```bash
make setup
uv run churnwatch packs                                # list packs
uv run churnwatch new-pack my-industry                 # start one from the template
uv run churnwatch generate --pack my-industry          # data, draft labels, rubric, pack README
uv run churnwatch score --pack my-industry             # report in out/my-industry/ (needs ANTHROPIC_API_KEY)
uv run churnwatch eval --pack my-industry --replay     # reproduce published scores, no API key
make budget                                            # spend so far and the worst case to finish
make test
```

How a pack gets built from a job description is in [`docs/new-pack.md`](docs/new-pack.md).

## What a pack controls, and what the engine controls

| The pack (`pack.toml`) | The engine (`src/churnwatch/`) |
| --- | --- |
| Product description, vendor name, target role | Account-health scenarios and how each moves the numbers |
| Billing unit (vehicle, pharmacy location, seat, store) and the column names it implies | The 50-account test mix: 22 at risk, 17 ambiguous by design |
| Customer segments, and which ones are seasonal | Prompt templates (`prompts/*.md.j2`), filled with the pack's vocabulary |
| Plans and per-unit prices | Structured output schema and the grounding check |
| Modules a customer can adopt | Rules baseline, metrics, results history |
| CSM-note wording for every scenario | Budget cap and reserve |
| Optional: industry-specific metrics, renamed columns, reworded churn reasons | Report |

## Design decisions

**Structured output with verifiable evidence, not free-text explanations.** The model returns JSON through a forced tool call (schema in [`schema.py`](src/churnwatch/schema.py)). Each reason must cite (field, value) pairs from the record, and notes must be quoted verbatim. [`grounding.py`](src/churnwatch/grounding.py) checks every citation against the source row. A CSM will repeat these explanations to a customer, so a made-up "usage down 38%" is worse than no explanation.

**A rules baseline as the bar to beat.** [`baseline.py`](src/churnwatch/baseline.py) is the threshold model a CS ops team could write in an afternoon. It sees only numbers and works on any pack through the pack's column names. If the LLM can't beat it, the LLM isn't worth its cost. The comparison also shows *where* the LLM adds value: reading the CSM notes.

**A test set built around the hard cases.** 17 of each pack's 50 test accounts are ambiguous on purpose, and they're scored separately.
- Some have numbers that look risky but are explained by the notes: a confirmed seasonal dip, a ramping rollout, a resolved incident, a replaced champion, or a late payment caused by the customer's own ERP migration.
- Others have healthy numbers with a risk only the notes reveal, such as a competitor pilot or an RFP.

**One engine, many packs.** Scenario behavior, the prompt, the schema, the metrics and the budget are shared. A pack can't quietly weaken the evaluation, and results compare across industries. Each pack writes its notes in its own industry's vocabulary, and those notes are where the LLM has to earn its place.

**Labels reviewed by a person against a written rubric.** Draft labels come from the generator's scenario. A person then reviews every row against the pack's `rubric.md`, which is generated from `pack.toml` before any model runs. Overrides are recorded in the labels file. Prompt v2 restates the rubric rather than being tuned on test failures.

**Every change is re-scored, and every run is kept.** [`evals.py`](src/churnwatch/evals.py) logs precision, recall, F1, ambiguous-case accuracy, reason accuracy, grounding, errors, cost and latency per pack and run. The [eval workflow](.github/workflows/eval.yml) runs on any change to `prompts/`, `packs/`, `src/` or `evals/config.toml`, and commits the new scores and tables.

**Cached, replayable responses.** Responses are cached by a hash of model, rendered prompt, schema, record and settings. Re-scoring after a label change costs nothing, and `--replay` reproduces every published number with no API key. The test workflow checks that it does.

## Cost and the spending cap

The repo has a **$25 hard cap on API spend across all packs**, set in [`evals/config.toml`](evals/config.toml) and enforced in [`budget.py`](src/churnwatch/budget.py):

- **Every paid call is logged** with its actual cost in [`evals/spend.csv`](evals/spend.csv), which is committed.
- **Runs are refused up front.** Before a run, the code adds up the worst case for every uncached call: an upper bound on input tokens plus the full `max_tokens` of output. It refuses the run, before anything is sent, if the worst case would pass the cap once added to:
  - the spend so far, and
  - the worst case of every pack's unfinished required runs (its eval runs and its demo).

  An experiment therefore can't eat the money needed to finish a pack.
- **Calls are capped one by one too.** Each call reserves its worst case before it is sent, so parallel calls can't overshoot. A call that fails in a way that might still have been billed is logged at its worst case.
- **Paid runs happen only in CI on `main`, one at a time,** so there is a single spend log.

Per pack, the required runs are Haiku 5.5 on prompts v1 and v2 and Sonnet 5.5 on v2 (50 test accounts each), plus Haiku 5.5 on the 200-account demo. That's **about $2.96 worst case per pack**, so the cap covers about 8 packs. Real calls cost less than the worst case. `make budget` shows the current numbers.

This cap only covers calls made through this code. The API key itself is also limited to $25 by a spend limit in the Claude Console.

## Limitations

- **Synthetic data.** Accounts and notes come from templates. Real CRM notes are messier, shorter and often missing. The numbers show the approach works on cases built to need judgment, not what it would score in production.
- **No outcome labels.** Labels encode what a reasonable CSM would conclude today, not whether the account churned later.
- **50 accounts per pack is small.** One account is 2 points of accuracy, so differences of a few points are noise.
- **One account at a time.** The model doesn't compare accounts or see history beyond the 90-day fields.

## Repo map

| Path | What it is |
| --- | --- |
| `packs/_template/pack.toml` | Commented template for a new industry pack |
| `packs/<slug>/` | One industry: `pack.toml`, generated data and labels, rubric, README, eval results |
| `prompts/*.md.j2` | Prompt versions, as templates the pack fills in |
| `src/churnwatch/` | Engine: pack loader, generator, scorer, grounding, baseline, evals, budget, report |
| `evals/config.toml` | Default runs, prices, budget cap |
| `evals/spend.csv` | Every paid API call and its cost |
| `evals/cache/` | Cached model responses (for replay) |
| `tests/fixtures/packs/` | A complete pack used only by the tests |

## About this project

I built this in November 2026 as a portfolio project. It was written with an AI coding assistant (Claude) under my direction. I set the scope, the evaluation design and the labeling rubric, reviewed each pack's labels by hand and reviewed the code.

MIT licensed.
