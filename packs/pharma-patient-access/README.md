# Pharma patient-access programs

<!-- Written once by `churnwatch generate`; edit freely. Only the RESULTS block is regenerated. -->

**Built for:** Senior Solutions Consultant, Gifthealth

Parcella Health runs direct-to-patient pharmacy and patient-support programs for pharmaceutical manufacturers. For each brand program, Parcella fills and ships prescriptions, runs benefits verification and prior-authorization support, applies copay assistance, and sends adherence outreach, and the manufacturer's brand team sees program data in a partner portal and a daily data feed. Manufacturers pay a program fee per contracted patient per year. A program does well when active patients track the manufacturer's enrollment forecast and patients get to their first fill quickly.

> Parcella Health and every account here are synthetic, generated from [`pack.toml`](pack.toml) by the churn-watch engine.

## Results

Scored on 50 labeled test accounts, 17 of them ambiguous by design (the numbers and the CSM notes point different ways).

<!-- RESULTS:START -->
| Model | Prompt | Precision | Recall | F1 | Ambiguous cases right | Main reason right | Evidence verified | Errors | Cost per 1,000 accounts | Median latency | Run |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Rules baseline | – | 58% | 86% | 69% | 0% | 90% | 100.0% | 0 | – | – | 2026-11-17 `b2cfe28` |

Labels: **draft** (generator drafts, not yet reviewed). 1 runs logged in [`evals/results.csv`](evals/results.csv); per-account errors in [`evals/misses.md`](evals/misses.md).
<!-- RESULTS:END -->

<!-- FINDINGS -->

## The data

The CRM export has one row per customer. The patients each customer licenses are the billing unit.

| Column | Meaning |
| --- | --- |
| `account_id` | Unique account ID. |
| `manufacturer` | Customer name. |
| `therapeutic_category` | The brand's therapeutic category. |
| `region` | Sales region. |
| `plan` | Subscription plan. |
| `arr_usd` | Annual recurring revenue in USD. |
| `customer_since` | Contract start date. |
| `renewal_date` | Next renewal date. |
| `days_to_renewal` | Days from the export date to renewal. |
| `contracted_patients` | Annual patient volume the manufacturer contracted for (its enrollment forecast). |
| `active_patients_now` | Patients with a fill or an open enrollment in the last 30 days. |
| `active_patients_90d_ago` | Active patients 90 days ago. |
| `weekly_portal_users_now` | Manufacturer brand-team users active in the partner portal in the last week. |
| `weekly_portal_users_90d_ago` | Weekly active portal users 90 days ago. |
| `services_adopted` | Program services in use, separated by semicolons. |
| `tickets_90d` | Support tickets from the brand team or about the program, last 90 days. |
| `sev1_tickets_90d` | Severity-1 incidents in the last 90 days: failed fills, copay rejects at scale, data-feed outages. |
| `open_escalation` | yes if the manufacturer has an open executive escalation. |
| `last_nps` | Most recent NPS response, 0 to 10; blank if none. |
| `champion_status` | active, or left if the manufacturer's program sponsor (brand director or patient-services lead) has left. |
| `invoice_days_overdue` | Days the oldest unpaid invoice is past due; 0 if none. |
| `days_since_exec_meeting` | Days since the last executive business review with the manufacturer. |
| `csm_notes` | Free-text notes from the client success manager. |
| `forecast_attainment_pct` | Derived: active patients as a % of contracted patients. |
| `active_patients_change_90d_pct` | Derived: % change in active patients over 90 days; null with no history. |
| `weekly_users_change_90d_pct` | Derived: % change in weekly portal users over 90 days; null with no history. |
| `median_days_to_first_fill` | Median days from enrollment to the patient's first shipped fill, last 30 days. |
| `refill_adherence_pct` | Share of active patients refilling on time (proportion of days covered of 80% or more), last 90 days. |


Files: [`data/accounts.csv`](data/accounts.csv) (demo export), [`data/testset/`](data/testset/) (test set and labels), [`rubric.md`](rubric.md) (how labels are decided), [`evals/misses.md`](evals/misses.md) (every miss).

## Run it

```bash
uv run churnwatch score --pack pharma-patient-access          # writes out/pharma-patient-access/report.html
uv run churnwatch eval --pack pharma-patient-access --replay  # reproduce the scores above, no API key
```
