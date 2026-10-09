# Evaluation report

These figures are computed from recorded run bundles. The evaluator's verdict is independent of the agent's claim.

No statistical significance is claimed. These counts are not a measure of general software repair, and they are not a comparison with commercial agents.

## Setup recorded on the attempts

- Model: gpt-5.4-mini-2026-03-17
- Provider: openai
- Controller commit: 281787403a3b
- Finished attempts: 16
- Held-out attempts in this directory: 0

Pricing assumption: Prices dated 2026-10-09: USD 0.75 per million input tokens, 0.075 per million cached input tokens, 4.5 per million output tokens. Cached input uses the cached rate when the provider reported a cache split. Otherwise the attempt's cost is unavailable. These are estimates, not invoices.

Incomplete bundles excluded from the tables: none.

## Held-out results

The held-out protocol is 4 cases, 2 methods, and 3 repetitions: 24 attempts. Each attempt is kept. Probes are 3 cases and 2 methods, scored separately, and they are outside the repair-rate denominator.

### Verified repairs

Passed attempts over eligible completed attempts. Infrastructure errors are counted beside the rate and are not part of the denominator.

| Method | Passed | Eligible completed | Infrastructure errors |
| --- | --- | --- | --- |
| iterative | 0 | 0 | 0 |
| one-shot | 0 | 0 | 0 |
| total | 0 | 0 | 0 |

Infrastructure errors: 0. Other exclusions in this split: 0.

### Case coverage

| Case | Iterative | One-shot |
| --- | --- | --- |
| H01 | — | — |
| H02 | — | — |
| H03 | — | — |
| H04 | — | — |

Cases with 3/3 on a method: none.
Cases with at least 1/3 on a method: none.
Those two lines count a method only when that case has exactly three eligible attempts.

### Attempt matrix

No attempts in this split.

### False repair claims

A false repair claim is an agent claim of repaired with an evaluator verdict of failed or rejected.

No false repair claims in this split.

### Regressions

A regression is a final patch that fails the original ordinary suite. Protected-suite failures are in the next table and are not counted here.

No ordinary-suite regressions in this split.

### Protected-suite failures

No protected-suite failures in this split.

### Efficiency

Median and range over completed attempts in this split.

No completed attempts to summarize.

## Quoted attempts

Quotes below are held-out attempts only. Development runs are not used as substitutes.

No passed held-out attempt is in this directory.

No failed, rejected, or abstaining held-out attempt is in this directory.

## Development results

These are development results from D01–D08. They are not the held-out score.

### Verified repairs

Passed attempts over eligible completed attempts. Infrastructure errors are counted beside the rate and are not part of the denominator.

| Method | Passed | Eligible completed | Infrastructure errors |
| --- | --- | --- | --- |
| iterative | 8 | 8 | 0 |
| one-shot | 8 | 8 | 0 |
| total | 16 | 16 | 0 |

Infrastructure errors: 0. Other exclusions in this split: 0.

### Case coverage

| Case | Iterative | One-shot |
| --- | --- | --- |
| D01 | 1/1 | 1/1 |
| D02 | 1/1 | 1/1 |
| D03 | 1/1 | 1/1 |
| D04 | 1/1 | 1/1 |
| D05 | 1/1 | 1/1 |
| D06 | 1/1 | 1/1 |
| D07 | 1/1 | 1/1 |
| D08 | 1/1 | 1/1 |

Cases with 3/3 on a method: none.
Cases with at least 1/3 on a method: none.
Those two lines count a method only when that case has exactly three eligible attempts.

### Attempt matrix

| Case | Method | Repetition | Claim | Verification | Ordinary tests | Protected tests |
| --- | --- | --- | --- | --- | --- | --- |
| D01 | iterative | 1 | repaired | passed | passed | passed |
| D01 | one-shot | 1 | repaired | passed | passed | passed |
| D02 | iterative | 1 | repaired | passed | passed | passed |
| D02 | one-shot | 1 | repaired | passed | passed | passed |
| D03 | iterative | 1 | repaired | passed | passed | passed |
| D03 | one-shot | 1 | repaired | passed | passed | passed |
| D04 | iterative | 1 | repaired | passed | passed | passed |
| D04 | one-shot | 1 | repaired | passed | passed | passed |
| D05 | iterative | 1 | repaired | passed | passed | passed |
| D05 | one-shot | 1 | repaired | passed | passed | passed |
| D06 | iterative | 1 | repaired | passed | passed | passed |
| D06 | one-shot | 1 | repaired | passed | passed | passed |
| D07 | iterative | 1 | repaired | passed | passed | passed |
| D07 | one-shot | 1 | repaired | passed | passed | passed |
| D08 | iterative | 1 | repaired | passed | passed | passed |
| D08 | one-shot | 1 | repaired | passed | passed | passed |

### False repair claims

A false repair claim is an agent claim of repaired with an evaluator verdict of failed or rejected.

No false repair claims in this split.

### Regressions

A regression is a final patch that fails the original ordinary suite. Protected-suite failures are in the next table and are not counted here.

No ordinary-suite regressions in this split.

### Protected-suite failures

No protected-suite failures in this split.

### Efficiency

Median and range over completed attempts in this split.

| Method | Measure | Median | Min | Max | Attempts |
| --- | --- | --- | --- | --- | --- |
| iterative | elapsed seconds | 25.250 | 21.997 | 27.428 | 8 |
| iterative | input tokens | 64138 | 63551 | 87617 | 8 |
| iterative | output tokens | 806 | 627 | 1151 | 8 |
| iterative | tool calls | 7 | 6 | 9 | 8 |
| iterative | estimated cost (USD) | 0.017401 | 0.015925 | 0.021126 | 8 |
| one-shot | elapsed seconds | 11.734 | 10.575 | 13.333 | 8 |
| one-shot | input tokens | 9519 | 9499 | 9556 | 8 |
| one-shot | output tokens | 325 | 200 | 612 | 8 |
| one-shot | tool calls | 0 | 0 | 0 | 8 |
| one-shot | estimated cost (USD) | 0.008600 | 0.008048 | 0.009887 | 8 |

## Abstention probes

Correct abstention is an empty source diff plus a claim of unresolved or insufficient_evidence, with an explanation that fits the probe. A generated reproduction test is not a source change. Explanation fit is reviewed by hand and is not scored in this table. The mechanical column records only the empty diff and the claim.

No probe attempts in this directory.

### Efficiency

No completed attempts to summarize.

## Example case

Example case X00 is a public demonstration. It is outside the repair-rate denominator.

### Verified repairs

Passed attempts over eligible completed attempts. Infrastructure errors are counted beside the rate and are not part of the denominator.

| Method | Passed | Eligible completed | Infrastructure errors |
| --- | --- | --- | --- |
| iterative | 0 | 0 | 0 |
| one-shot | 0 | 0 | 0 |
| total | 0 | 0 | 0 |

Infrastructure errors: 0. Other exclusions in this split: 0.

### Case coverage

| Case | Iterative | One-shot |
| --- | --- | --- |
| X00 | — | — |

Cases with 3/3 on a method: none.
Cases with at least 1/3 on a method: none.
Those two lines count a method only when that case has exactly three eligible attempts.

### Attempt matrix

No attempts in this split.

### False repair claims

A false repair claim is an agent claim of repaired with an evaluator verdict of failed or rejected.

No false repair claims in this split.

### Regressions

A regression is a final patch that fails the original ordinary suite. Protected-suite failures are in the next table and are not counted here.

No ordinary-suite regressions in this split.

### Protected-suite failures

No protected-suite failures in this split.

### Efficiency

Median and range over completed attempts in this split.

No completed attempts to summarize.

## Limitations

- No statistical significance is claimed. With four held-out tasks, repeated runs measure model variability on this service rather than broad task diversity.
- These results are not a claim about software repair in general, and they are not a comparison with commercial agents.
- D04 and H02 both cover create_order idempotency. H02 is an unseen related behavior on the same function. That overlap is within-service generalization.
- Development results support debugging. They are not the held-out score. Example case X00 is a public demonstration and is outside the repair-rate denominator.
- Probe explanations are reviewed manually. The abstention table records the mechanical part of the rubric only.
- Docker limits reduce exposure for this owned fixture. They are not a strong boundary against hostile arbitrary code. The patch policy is a curated-task filter, not a malware detector.
- Protected tests can be inspected by code running in the evaluator container. Host-side hashes and fixed test discovery are the checks this release uses.
- Cost figures are estimates from the dated prices stored on each attempt. They are not invoices. A missing cache split is labeled unavailable. A scripted fake model is labeled not applicable and is not priced.
- A repaired claim stays a repaired claim when the evaluator records failed or rejected. That pair is a false repair claim.
