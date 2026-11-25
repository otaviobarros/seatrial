# Statistics

seatrial reports each comparison as one reading built from these methods:

- **Fisher's exact test** decides the verdict. It is the only gate.
- **Wilson score intervals** show how precise each pass rate is.
- **Newcombe intervals** show how large the change between revisions is.
- **A sample-size estimate** says how many trials would settle a result that the
  run could not decide.
- **Mann-Whitney tests** compare cost, turns, tool errors, and wall time, and
  add a `cheaper` or `costlier` note to the verdict.
- **A pooled skill verdict** combines a skill's cases with an exact stratified
  test, so small gains that no single case can show add up to one result.

`summary.json` stores the reading under `interpretation` for each case and host,
the effect and cost note under `decision`, and the cost comparison under
`efficiency`; every report format prints them. The verdict rule never reads the
intervals or the estimate; they explain the verdict and say what to do next.

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

## Pooled skill verdict

Each case decides on its own trials, capped at about 10 per arm, so only large
changes reach significance. A skill with five cases that each gain 30 points
gets five undecided verdicts, though the edit helps. The pooled verdict asks one
question per skill: across all its cases and hosts, does head pass the primary
assertion more often than base?

### The test

Each case and host is a stratum with its own 2×2 table on that case's primary
assertion: in stratum k, base passes c₁ₖ of n₁ₖ valid trials and head passes
c₂ₖ of n₂ₖ, with mₖ = c₁ₖ + c₂ₖ passes in Nₖ = n₁ₖ + n₂ₖ trials. Cases differ
in difficulty, so strata are never merged into one table; each stratum compares
base with head only inside itself.

The verdict uses the exact conditional test of a common odds ratio of 1, the
stratified form of Fisher's test. With every stratum's totals fixed, head
passes in stratum k follow the hypergeometric distribution

```text
P(X = x) = C(n₂ₖ, x) · C(n₁ₖ, mₖ − x) / C(Nₖ, mₖ)
```

The strata are independent, so the total T = Σ c₂ₖ follows the convolution of
these distributions. The two-sided p-value sums the probabilities of every total
no more likely than the observed one, the rule Fisher's test uses. With one
stratum it is exactly Fisher's p-value on the primary assertion. The verdicts
can still differ: a case verdict also reads its other assertions and
zero-tolerance rules, and a correction adjusts it in a different family. It needs no large-sample approximation,
so it stays valid at 10 trials per arm.

The pooled verdict, at the run's threshold α (0.05, or the per-look threshold
of a sequential run):

| Pooled verdict | Rule |
|---|---|
| `improved` | p < α, and head passed more often than its expectation E = Σ n₂ₖ·mₖ/Nₖ |
| `regressed` | p < α, and head passed less often than E |
| `inconclusive` | Any other result, or no stratum can inform the test |

`shift` records the direction: `higher` when T > E, `lower` when T < E.

### Strata left out

A stratum with all passes or all failures in both arms has one possible table,
so it carries no information: 10/10 against 10/10 cannot favor either arm. A
comparison whose case verdict has a `reason`, such as fewer than 80% valid
trials, stays out too, as it does from the case verdicts. Both are listed under
`excluded` with the reason `no_variation` or `insufficient_trials`.

### Reported beside the test

- **Mantel-Haenszel odds ratio.** The common odds ratio of passing in head
  against base, with a = c₂ₖ, b = n₂ₖ − c₂ₖ, c = c₁ₖ, d = n₁ₖ − c₁ₖ:

  ```text
  OR = Σ (aₖdₖ/Nₖ) / Σ (bₖcₖ/Nₖ)
  ```

  Above 1 favors head. The 95% interval is exp(log OR ± 1.96·SE) with the
  Robins-Breslow-Greenland variance of log OR, which stays consistent when there
  are many small strata. When no stratum has a head failure beside a base pass,
  the estimate is unbounded and stored as `null`; when no stratum has a head
  pass beside a base failure, it is 0. Neither has an interval.
- **Cochran-Mantel-Haenszel chi-square.** The large-sample statistic

  ```text
  X² = (|T − E| − ½)² / Σ n₁ₖn₂ₖmₖ(Nₖ − mₖ) / (Nₖ²(Nₖ − 1))
  ```

  on 1 degree of freedom, with the continuity half dropped when |T − E| < ½,
  as R's `mantelhaen.test` does. It is reported under `cmh` for readers who
  know it; the verdict reads only the exact test.

Reference values, from the penicillin and rabbits data in R's
`mantelhaen.test` documentation (5 strata, 2 without variation): X² = 3.93,
p = 0.047; exact p = 0.040; OR = 7.0 (95% interval 1.03 to 47.7). The unit
tests check these values.

### Example

Five cases, each 5/10 in base and 8/10 in head. Each Fisher test gives
p = 0.35, so each case is `non-inferior` with an `underpowered` note. Pooled,
base passes 25/50 and head 40/50, E = 32.5, exact p = 0.004, OR = 4.0 (95%
interval 1.6 to 9.7): the skill verdict is `improved`.

### Corrections and sequential runs

The pooled tests are a separate family from the case tests: one test per
skill. `--correction holm` or `bh` adjusts them across the run's skills with
the same method, and leaves out a skill whose strata cannot reach p < α, as the
case family does. `adjusted_p_value` and `verdict_uncorrected` work as they do
for cases, and reports print the uncorrected verdict beside a changed pooled
one. `family_size` counts the pooled tests, and the correction note names it.

A sequential run tests the pooled verdict once, after the run, at the per-look
threshold. The stop rule looks only at single cases, so a stratum that stopped
early is pooled with its trials so far. This keeps the pooled test as strict as
every other verdict in the run, but it is not a formal sequential design for the
pooled test. For a pooled claim that must hold exactly, run a fixed `--n`.

### What the pooled verdict does not do

- It is reported, never gated. The case verdicts, the `regressed` list, and
  `--fail-on-regression` do not read it.
- A pooled `improved` does not cancel a case `regressed`. `case_verdicts`
  counts the case verdicts beside it; read both.
- It assumes one common odds ratio across strata. When cases measure different
  things, their effects can differ in size or direction: a large gain in one
  case and a loss in another can cancel or let the gain hide the loss.
  `directions` counts strata where head passed more, less, or as often as
  base. A split there means the pooled result describes no single case.
  seatrial does not test homogeneity: at 10 trials per arm the Breslow-Day
  test has little power and its approximation fails.
- It reads only each case's primary assertion. A zero-tolerance failure or a
  drop on another assertion shows in the case verdicts, not here.
- It weighs each stratum by its information, not by importance, and pools every
  case of the skill in the run, holdout cases included. To keep holdout cases
  apart, run them in their own run.

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

## Effect size: Newcombe intervals

A Wilson interval describes one pass rate. The effect describes the change: head
rate minus base rate, with a 95% interval for that difference. seatrial uses
Newcombe's hybrid score interval (method 10), built from the two Wilson
intervals [l₁, u₁] for base rate p₁ and [l₂, u₂] for head rate p₂:

```text
d    = p₂ − p₁
low  = d − √( (p₂ − l₂)² + (u₁ − p₁)² )
high = d + √( (u₂ − p₂)² + (p₁ − l₁)² )
```

Like the Wilson interval, it stays inside [−1, 1] and keeps a nonzero width at
0 of n and n of n, where the textbook difference interval collapses.

| Base | Head | Effect | 95% interval |
|---|---|---|---|
| 10/10 | 7/10 | −30 pp | −60 to +4 |
| 10/10 | 2/10 | −80 pp | −94 to −38 |
| 7/10 | 7/10 | +0 pp | −36 to +36 |
| 0/10 | 10/10 | +100 pp | +61 to +100 |

The first two rows are both `regressed` under a zero-tolerance assertion; the
effect shows that the second is far larger. An interval that spans 0 fits no
change, and its far end is the largest change the run cannot rule out: at 7/10
against 7/10, a drop of 36 points still fits the trials.

`decision.effect` holds the entry for every assertion and
`decision.effect_primary` for the primary one. The interval is not a test and
does not use the multiple-comparison correction or the per-look threshold. At
small n it can exclude 0 while Fisher's test is not significant: 6/10 against
10/10 gives −40 pp (−69 to −4) with p = 0.087. The verdict reads only Fisher.

## Cost, turns, and tool errors

For each comparison, seatrial compares four values per valid trial between
arms: host-reported `cost_usd`, `num_turns`, `tool_errors`, and `wall_s`. It
uses the two-sided Mann-Whitney rank test with a normal approximation, as for
lines added, and stores both medians, the rank direction `shift`, and the
p-value under `efficiency`. A host that does not report a value leaves it out;
an arm with no values has no shift or p-value. The note takes its direction
from `shift`, because skewed costs can move the medians the other way.

`num_turns` is the count the host reports. The Claude adapter takes it from the
last result event, so a trial that waits for a background task counts only its
last segment.

| Note | Rule |
|---|---|
| `cheaper` | The verdict is `non-inferior`, and head costs rank lower with p < 0.05 |
| `costlier` | Any verdict with p-values, and head costs rank higher with p < 0.05 |

The note appears beside the verdict, for example `non-inferior, cheaper`, and in
`decision.cost`. It never changes `decision.verdict`, the `regressed` list, or
`--fail-on-regression`. The cost test runs at 0.05 without a correction or
per-look threshold, so read it as a secondary result. At 10 trials per arm the
rank test detects only a large, consistent shift in cost.

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
Holm and Benjamini-Hochberg. The interpretation records α in `alpha`. For 60%
against 80% across a family of five tests, α is 0.01 and the estimate rises
from 82 to about 122 trials.

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
- `cheaper` and `costlier` rest on one uncorrected rank test per comparison;
  in a large run, expect a few by chance.
- The pooled skill verdict assumes a common odds ratio across cases, reads only
  the primary assertions, and is never gated; see
  [what it does not do](#what-the-pooled-verdict-does-not-do).
- Verdicts are advisory, not merge gates. `--fail-on-regression` is the only exit
  code that reads them.

[Using seatrial from an agent](agents.md) lists the JSON fields.
