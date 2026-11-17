# Building a pack from a job description

A pack points churn-watch at one industry. Building one takes about an hour of review time and costs at most about $3 of API spend.

## 1. Read the posting for four things

| Look for | It becomes |
| --- | --- |
| What the company sells, and to whom | `[pack] product`, `[unit]`, `[[plans]]`, `[features]` |
| Who its customers are | `[[segments]]`, and which segments are seasonal |
| What the role cares about (retention, expansion, adoption, compliance) | Wording in `[notes]` and `[reasons]`, and which `extra_fields` to add |
| The posting's own vocabulary | Column names (`[unit] column`, `[columns]`) and note wording |

Use an invented vendor name, never the real company's. The target company and role go in `target_company` and `target_role`, which only label the pack.

## 2. Write the pack

```bash
uv run churnwatch new-pack <slug>      # copies packs/_template/pack.toml
```

Fill in every section. The notes do the most work: write two or three variants per scenario in the voice of a CSM in that industry. The ambiguous scenarios matter most:
- `seasonal_dip`
- `ramping`
- `resolved_incident`
- `champion_replaced`
- `ap_delay`
- `competitor`

In each of those, the note has to explain the numbers away, or reveal a risk the numbers hide.

## 3. Generate and read the data

```bash
uv run churnwatch generate --pack <slug>
```

This writes the export, the 50-account test set, the draft labels, `rubric.md` and the pack README. Read 10 or so rows of `data/testset/accounts.csv`. If a note reads wrong for the industry, fix `pack.toml` and generate again.

## 4. Review the labels

Go through every row of `data/testset/labels_draft.csv` against `rubric.md`:
- Change `at_risk`, `main_reason` or `ambiguous` where you disagree.
- Set `reviewed` to `yes`, and say why in `reviewer_note` when you change a label.
- Save the result as `data/testset/labels.csv`.

From then on, the evals use those labels. `generate` refuses to overwrite a pack with reviewed labels unless you pass `--force`.

## 5. Check the budget, then push

```bash
uv run churnwatch budget      # the new pack's required runs must fit
```

Push to `main`. The eval workflow scores the pack and commits its results. Then run the demo workflow from the Actions tab to publish the pack's sample report.

## 6. Write the findings

Fill in the `<!-- FINDINGS -->` section of the pack README with what the scores show:
- where the LLM beat the baseline
- what it still misses (from `evals/misses.md`)
- what it costs per 1,000 accounts
