# Labeling rubric: Pharma patient-access programs

<!-- Generated from pack.toml by `churnwatch generate`. Edit pack.toml, not this file. -->

Each of the 50 test accounts gets two labels:

- **at_risk** (`yes` / `no`): would a reasonable CSM put this account on this week's
  call list because it could fail to renew or shrink materially at renewal?
- **main_reason**: the single biggest driver of that risk, from the list below. `none`
  when `at_risk` is `no`.

Labels describe the account *as of the export date (2026-11-16)*, using only
what is in the export row. They are not predictions checked against a later outcome:
this is synthetic data, and the account has no future.

## Reasons

| main_reason | Use when |
| --- | --- |
| `usage_decline` | Active patients or new starts fell sharply (about 25% or more in 90 days) and nothing in the notes explains it away. |
| `champion_loss` | The manufacturer's program sponsor (brand director or patient-services lead) left, and no engaged successor is named. |
| `support_issues` | Repeated severe incidents (failed fills, prior-auth or copay failures, data-feed outages) or an open escalation, and the brand team is still unhappy. |
| `billing` | Program-fee invoices well overdue and signs the manufacturer can't or won't pay (not a procurement-system delay). |
| `competitor` | Evidence of evaluating another hub, specialty pharmacy or patient-services vendor: an RFI or RFP, a price comparison, or a competing pilot. |
| `budget_or_downsizing` | The brand is losing exclusivity, being restructured, or having its program budget cut, and the manufacturer wants to reduce scope. |
| `low_adoption` | A partner for 12+ months with active patients far below forecast and few services adopted. |
| `none` | Not at risk. |

## Tie-breaks

1. **Notes beat numbers when they explain the numbers.** A confirmed seasonal dip (Seasonal allergy),
   a new customer still rolling out, an incident the customer accepted as resolved, a
   departed champion with a named and engaged successor, or a late payment caused by the
   customer's own finance-system change: not at risk.
2. **Notes beat numbers when they reveal a risk the numbers hide.** A competitor pilot,
   a price comparison or an RFP on otherwise healthy metrics: at risk.
3. **A large drop with no notes is at risk**, even in a seasonal segment. Nobody has
   confirmed it is seasonal.
4. When two reasons apply, pick the one a CSM would open the call with. Usually that is
   the one the customer said out loud (notes) over the one inferred from metrics.

## Ambiguous flag

Mark `ambiguous = yes` when the metrics alone point the other way from the label.
These cases are reported separately, because they are where a rules model fails and
where the LLM has to earn its place.

## Provenance

Draft labels come from the scenario the generator used for each account
(`generator_scenario` in `data/testset/labels_draft.csv`). A person then reviews every
row against this rubric, may override it, and records why in `reviewer_note`. The
reviewed file is `data/testset/labels.csv`; evals use it once it exists.
