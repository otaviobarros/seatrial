# Statistics

seatrial reports each comparison as one reading built from three methods:

- **Fisher's exact test** decides the verdict. It is the only gate.
- **Wilson score intervals** show how precise each pass rate is.
- **A sample-size estimate** says how many trials would settle a result that the
  run could not decide.

`summary.json` stores the reading under `interpretation` for each case and host,
and every report format prints it. The verdict rule never reads the intervals or
the estimate; they explain the verdict and say what to do next.

## Why agent evaluations need this

An agent is not deterministic. The same skill, prompt, and model pass on one run
and fail on the next. One trial per revision measures luck, and the usual budget
of 3 to 10 trials leaves wide uncertainty: 7 passes in 10 trials is consistent
with a true pass rate anywhere from 40% to 89%. A report that shows `7/10` beside
`9/10` invites the reader to see an improvement that the data cannot support.
seatrial shows that uncertainty, tests the difference exactly, and turns an
undecided result into a concrete trial count.

## The verdict: Fisher's exact test

For each assertion, the two revisions give a 2×2 table: passes and failures in
base, passes and failures in head. Fisher's exact test computes the probability
of a table at least as extreme as the observed one if both revisions had the same
pass rate, summed over every table with the same totals (two-sided). It uses the
exact hypergeometric distribution, so it stays valid at 3 or 10 trials, where
tests that rely on a normal approximation are not.

The verdict rule, applied per case and host:

| Verdict | Rule |
|---|---|
| `inconclusive` | Fewer than 80% of the trials in a revision are valid; nothing else is tested |
| `regressed` | A zero-tolerance assertion passed in every base trial and failed in a head trial, or any assertion is worse with p < 0.05 |
| `improved` | The primary assertion is better with p < 0.05, and head has no zero-tolerance failure |
| `non-inferior` | No assertion drops by more than two trials' worth, and head has no zero-tolerance failure |
| `inconclusive` | Any other result |

p < 0.05 means a difference this large would appear by chance less than 5% of
the time if the revisions behaved the same. Fisher's test is conservative: its
real false-positive rate is at or below 5%, so a verdict that rests on a p-value
errs toward caution. A zero-tolerance `regressed` rests on one failure, not on a
test: a flaky failure triggers it.

### Many comparisons

Each assertion, case, and host is a separate test, so a large run makes many.
`--correction holm` or `--correction bh` adjusts the p-values across the run
before the verdict rule reads them; the rule then compares the adjusted p-value
with 0.05. Holm controls the chance of any false verdict; Benjamini-Hochberg
controls the share of false verdicts among the significant ones and keeps more
power. The family counts only tests whose margins can reach p < 0.05: 10/10
against 10/10 cannot, so it stays out. A verdict the correction changed shows
`*`, keeps the raw verdict in `verdict_uncorrected`, and the interpretation is
rebuilt from the corrected verdict.

### Sequential trials

With `--looks`, seatrial checks the verdict after each listed trial count and
stops a case once it is decided. Each look spends an equal share of the 5%
(Bonferroni), so the per-look threshold is 0.05 divided by the number of looks,
and every verdict, evidence level, and trial estimate uses it in place of 0.05.
Under a correction, the stop gate also divides by the number of assertions in
the run. The non-inferiority margin keeps the planned n, so an early stop never
widens it. Reports show where a case stopped, for example `regressed (n=6
significant)`, and mark a stop a regrade or correction contradicts as
`superseded`.

## Precision: Wilson score intervals

For c passes in n trials, with rate p̂ = c / n and z = 1.96 for 95% confidence,
the Wilson interval is

```text
centre = (p̂ + z²/2n) / (1 + z²/n)
half   = z / (1 + z²/n) · √( p̂(1 − p̂)/n + z²/4n² )
CI     = [centre − half, centre + half]
```

The textbook interval p̂ ± z·√(p̂(1 − p̂)/n) fails at the sizes seatrial runs: at
10 of 10 it has zero width and claims certainty, and near 0 or 1 it extends past
0% or 100%. The Wilson interval stays inside [0, 1], has nonzero width at 0 of n
and n of n, and covers the true rate close to 95% of the time even at small n.

| Passes | Rate | 95% interval |
|---|---|---|
| 0/10 | 0% | 0–28% |
| 5/10 | 50% | 24–76% |
| 7/10 | 70% | 40–89% |
| 10/10 | 100% | 72–100% |

`margin` is half the interval width. At a 50% pass rate it is about ±26 points
at 10 trials, ±17 at 30, and ±10 at 100: quadrupling the trials roughly halves it.

## Next step: trials needed

When the primary difference is not significant, seatrial estimates how many
trials per revision a test would need to detect the observed difference with 80%
power at a two-sided level α, 5% by default. With base rate p₁, head rate p₂,
p̄ their mean, and z the two-sided normal quantile for α (1.96 at 5%):

```text
n = ( z·√(2·p̄(1 − p̄)) + 0.8416·√(p₁(1 − p₁) + p₂(1 − p₂)) )² / (p₁ − p₂)²
```

This is the standard normal-approximation sample size for two proportions; for
60% against 80% it gives 82 trials. Fisher's test needs slightly more trials than
this estimate, so treat `trials_needed` as a floor. When the rates are equal,
there is no difference to size, and `detectable_change` gives the smallest change
the run could have detected instead: about 59 points from a 70% base at 10 trials.

For `inconclusive`, the estimate sizes the assertion with the largest drop rather
than the primary assertion.

A sequential run uses its per-look threshold as α. Under a correction, α is
also divided by the family size, a Bonferroni bound that is conservative for
Holm and Benjamini-Hochberg. The interpretation records α in `alpha`. For 60% against 80% across a family of five tests, α is
0.01 and the estimate rises from 82 to about 122 trials.

A result is `underpowered` when the difference is not significant and the run had
fewer trials than `trials_needed`. A `non-inferior` result with equal rates is not
underpowered: the regression guard it needed already holds. Above 1000 trials,
the recommendation states the smallest change the run could detect instead of a
trial count; a difference that small does not show the revisions are equivalent.
`[limits] max_n` caps `--n` at 10 by default; raise it before a larger run.

## How to read a result

| `evidence` | Meaning | What to report |
|---|---|---|
| `zero_tolerance` | An assertion that must never fail newly failed, or failed in head and base alike | A regression in the first case; in the second, an `inconclusive` that more trials do not change. Fix the assertion first |
| `strong` | p < 0.01 | The change is real; quote both intervals |
| `significant` | p < 0.05 | The change is likely real; quote both intervals |
| `not_significant` | p ≥ 0.05 | No detected effect; if `underpowered`, the run was too small to decide |
| `insufficient_trials` | Too many invalid trials | Fix the invalid statuses, then run again |

Under a correction, the p-value in this table is the adjusted one. A sequential
run replaces 0.05 with its per-look threshold and 0.01 with a fifth of it.

Overlapping intervals and a non-significant p-value mean the run cannot tell the
revisions apart. That is not evidence that they behave the same: at 10 trials only
large changes are visible. `non-inferior, underpowered` reads as "no drop of more
than two trials, and no detected gain".

Example: base passes 6 of 10 (31–83%), head 8 of 10 (49–94%), Fisher p = 0.63.
The verdict is `non-inferior`, the intervals overlap, and about 82 trials per
revision would detect a 20-point gain. Report it as "no detected effect at 10
trials", not as an improvement.

## Assumptions and limits

- Trials are independent and run under the same conditions. seatrial alternates
  revisions so that time and load affect both alike.
- Each assertion, case, and host is a separate test. Across 20 comparisons of
  identical revisions, about one can reach p < 0.05 by chance. Use
  `--correction holm` or `--correction bh` for a run with many comparisons, or
  confirm a surprising verdict with a second run.
- The trial estimate sizes one assertion: the primary one, or for `inconclusive`
  the one with the largest drop.
- Verdicts are advisory, not merge gates. `--fail-on-regression` is the only exit
  code that reads them.

[Using seatrial from an agent](agents.md) lists the JSON fields.
