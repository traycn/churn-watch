# Labeling rubric

Each of the 50 test accounts gets two labels:

- **at_risk** (`yes` / `no`): would a reasonable CSM put this account on this week's
  call list because it could fail to renew or shrink materially at renewal?
- **main_reason**: the single biggest driver of that risk, from the list below. `none`
  when `at_risk` is `no`.

Labels describe the account *as of the export date (2026-11-16)*, using only what is
in the export row. They are not predictions checked against a later outcome: this is
synthetic data, and the account has no future.

## Reasons

| main_reason | Use when |
| --- | --- |
| `usage_decline` | Active vehicles or weekly users fell sharply (roughly 25%+ in 90 days) and nothing in the notes explains it away. |
| `champion_loss` | The person who bought or owns the relationship left, and no engaged successor is named. |
| `support_issues` | Repeated severe incidents or an open escalation, and the customer is still unhappy. |
| `billing` | Invoices well overdue *and* signs of unwillingness or inability to pay (not a process delay). |
| `competitor` | Evidence of evaluating or piloting another vendor, a price-comparison request, or an RFP. |
| `budget_or_downsizing` | The customer is cutting fleet, drivers or budget and has asked to reduce scope. |
| `low_adoption` | Customer for 12+ months with low active use and few features, never fully onboarded. |
| `none` | Not at risk. |

## Tie-breaks

1. **Notes beat numbers when they explain the numbers.** A seasonal fleet that dips
   every winter and renewed early last year is not at risk, even with a 40% usage drop.
   A ramping new customer with low usage is not at risk.
2. **Notes beat numbers when they reveal a risk the numbers hide.** A competitor pilot
   on otherwise healthy metrics is at risk.
3. **A resolved incident is not a risk** if the notes say the customer accepted the fix.
4. **A departed champion with a named, engaged successor is not a risk.**
5. **A late payment caused by the customer's own process change is not a risk** if a
   PO or payment date is confirmed.
6. When two reasons apply, pick the one a CSM would open the call with. Usually that is
   the one the customer said out loud (notes) over the one inferred from metrics.

## Ambiguous flag

Mark `ambiguous = yes` when the metrics alone point the other way from the label.
These cases are reported separately in the results, because they are where a rules
model fails and where the LLM has to earn its place.

## Provenance

Draft labels come from the scenario the generator used to create each account
(`generator_scenario`). A person then reviewed every row against this rubric and could
override it; overrides are recorded in `reviewer_note`. The final file is
`data/testset/labels.csv`.
