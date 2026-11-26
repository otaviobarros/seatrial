# Using seatrial from an agent

Drive seatrial through `--json` and exit codes. Prompts appear only when stdin
and stdout are a terminal; every value has a flag.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The run stopped early (usage limit, sign-in, time, or cost cap) |
| 2 | Usage error: bad option, unknown case, or a cap exceeded |
| 3 | Configuration error, or a failed `doctor` check |
| 4 | A `regressed` verdict with `--fail-on-regression` |
| 5 | `--changed REF` found no affected case |

## JSON output

With `--json`, stdout holds one JSON document. During `run` and `compare`, stderr
holds one JSON line per finished trial:

```json
{"event": "trial", "finished": 3, "scheduled": 8, "case_id": "demo-001", "host": "claude", "arm": "head", "trial_index": 1, "status": "completed", "pass": true, "wall_s": 41.2, "cost_usd": 0.03}
```

`scheduled` counts the trials submitted so far. In a sequential run it grows with
each look, so `finished` equal to `scheduled` does not mean the run is complete.

An error prints `{"ok": false, "error": "...", "exit": 2}` on stdout, with
the exit code it names.

The final document of `run`, `compare`, and `regrade` has `run_dir`,
`stopped_early`, `regressed` and `underpowered` (lists of `"<case> <host>"`), and
`summary`, whose `revisions` maps each arm to the revision it ran.
`seatrial schema --json` prints the case, evidence, status, verdict,
interpretation, and exit-code contract.

Each comparison in `summary.cases.<id>.hosts.<host>` has `decision` (the verdict
and Fisher p-values) and `interpretation`, which reads it for people and tools:

| Field | Value |
|---|---|
| `verdict` | Same as `decision.verdict`, after any correction |
| `evidence` | `zero_tolerance`, `strong` (p < 0.01), `significant` (p < 0.05), `not_significant`, or `insufficient_trials`; under a correction, p is the adjusted p-value. A sequential run compares p with its per-look threshold instead of 0.05 |
| `primary.base`, `primary.head` | `passes`, `valid`, `rate`, `ci` (95% Wilson `[low, high]`), `margin` (half the interval) |
| `primary.difference`, `primary.p_value` | Head rate minus base rate; Fisher p on the primary assertion |
| `primary.intervals_overlap` | `true` when the base and head intervals overlap |
| `deciding_assertion`, `deciding_p_value` | The assertion the reading rests on and its gate p-value: adjusted under a correction, else Fisher. For `inconclusive`, the assertion with the largest drop. With `zero_tolerance` evidence, the failing zero-tolerance assertion, and the p-value is `null` |
| `underpowered` | `true` when the difference is not significant and the run had fewer trials than `trials_needed` |
| `trials_needed` | Trials per revision to detect the observed change with 80% power, on the primary assertion or, for `inconclusive`, on `deciding_assertion`; `null` when there is no change, or with `zero_tolerance` evidence on an `inconclusive` result |
| `detectable_change` | The smallest change, as a fraction, that the run's trial count could detect in at least one direction, on the same assertion as `trials_needed`; `null` when no change up to 100 points could be detected |
| `alpha` | The per-test threshold behind `trials_needed`: `multiple_comparisons.alpha` (0.05, or the per-look threshold of a sequential run), divided by `family_size` under a correction |
| `explanation`, `recommendation` | The same reading as text; the `markdown` and `comment` formats print these |

Every arm summary also has `primary_ci` and `assertion_ci` (`{assertion: [low, high]}`),
and `<metric>_values` and `<metric>_median` for `cost_usd`, `num_turns`,
`tool_errors`, and `wall_s` over valid trials. A comparison adds `efficiency`:
per metric, `base_median`, `head_median`, `shift` (`higher`, `lower`, or `none`:
where head values rank), and the Mann-Whitney `p_value`; `shift` and `p_value`
are `null` when an arm has no values. `decision.cost` is `cheaper`, `costlier`, or `null`
and never changes `decision.verdict`. `decision.effect` maps each assertion to
`{estimate, ci, level, metric}`: head minus base pass rate with its 95% Newcombe
interval, as fractions; `decision.effect_primary` repeats the primary entry. A
decision with a `reason` has no effect.
[Statistics](statistics.md) explains the method.

For a pull request comment, use `--format comment` on `run`, `compare`, or
`report`. Without `--json`, stdout holds only the Markdown; progress and the run
directory go to stderr. `--summary-file PATH` appends the same Markdown to PATH
with any format, with or without `--json`. [Continuous integration](ci.md) has
platform recipes.

`summary.multiple_comparisons` names the correction (`method`), the number of
Fisher tests it covered (`family_size`), and `alpha`. The family leaves out
tests whose pass counts cannot reach p < `alpha`. A decision with a `reason`,
such as too few valid trials, has no p-values. A new zero-tolerance failure
stays `regressed` under every method and keeps its p-values in the family.
Other decisions keep raw `p_values`; with a method other than `none` they add
`adjusted_p_values`, and `verdict_uncorrected` when the correction changed the
verdict. A tool should read these fields only when they are present. The `regressed`
list and exit code 4 use the corrected verdicts.

A `compare --sequential` run adds a top-level `sequential` object and, in each
decision, a `sequential` record with every look's `p_values` and `outcome`,
`stopped_at`, `stop_reason`, and `trials_saved`. `stopped_at` is null when a
budget or deadline ended the run first. `superseded` is true when a regrade or
a later correction gave a verdict the stop reason contradicts. `trials_saved`
counts trials the stop rule skipped; `trials_skipped` counts trials a budget,
deadline, or stopping error skipped. `seatrial schema --json` lists every key
under `summary_fields`. `--dry-run` counts the trials at most; the run usually
takes fewer.

`summary.label` is `a/a` when both arms mounted identical files, `a/b` for two
different revisions, and `null` for a single-revision run. An A/A run adds
`summary.aa` (`comparisons`, `noisy` as a list of `"<case> <host>"`, and
`max_difference`) and, per comparison, `noise`: `difference` (absolute primary
pass-rate difference), `max_difference` and `max_assertion` (the largest over
all assertions), `verdict`, and `noisy` (true for `improved` or `regressed`).
With `--noise-floor`, the summary keeps the reference under `noise_reference`
and each comparison it covers gets `noise_floor`: `difference` (the largest A/A
drift), `noisy`, `runs`, `observed` (this run's absolute primary difference),
and `within` (`observed` at or below `difference`); it is `{stale: true}` alone
when the case's grader changed since the A/A runs. The noise floor never
changes `decision.verdict`, the `regressed` list, or exit code 4. An A/A run
keeps them too: with `--fail-on-regression`, a noisy `regressed` case exits 4.

## Trend history

`seatrial trend --json` prints a list of rows, oldest first within each series
of case, host, and arm. Each row summarizes one arm of one finished run: the
identity fields (`skill_tree`, `grader_sha256`, `fixture_sha256`, `model`, `cli_version`,
`revision`), the counts (`trials`, `valid`, `primary_passes`, `primary_ci`,
`all_pass`), `cost_usd_median`, `verdict`, `label` (`a/a` for a noise-floor
run, whose verdict measures noise, not a change), and `run_dir`. `changes` lists each
identity field that differs from the previous row of the series as
`{field, label, from, to}`; the first row has none. `seatrial schema --json`
lists every field under `history_fields`. Rows from other project roots never
appear. Filters (`--case`, `--skill`, `--host`, `--arm`, `--since`, `--last`)
do not reset `changes`: a row's marker still compares it with the run before it.

## Procedure

1. Run `seatrial doctor --json`. On exit 3, report each check whose `level` is
   `fail` and stop.
2. Select cases with `seatrial list --json` and the selectors the task names.
   Cases with `"holdout": true` run only with `--holdout`; run them once, after
   the skill edit is final, and report them apart from the tuning cases.
   A case whose `error` is not null cannot load its `case.py`; report the error
   and leave the case out.
   To select the cases a change affects, add `--changed REF`, for example
   `--changed origin/main`; each case then has `selected_because`. Exit 5
   means no case is affected: report that and run nothing.
   To measure whether a skill helps at all, compare with `--base NONE`. Text
   reports label that arm `none`; JSON keeps `base` and sets
   `summary.revisions.base.name` to `"NONE"`. To measure whether the agent picks
   a skill on its own, add `--trigger auto`. To measure run-to-run noise,
   compare a revision with itself (`--base main --head main`) and report the
   `noisy` cases apart from the verdicts.
3. Print the plan with `--dry-run --json`. Confirm that `count` fits the task's
   budget before any native call. A selected case that cannot load stops the
   plan with exit 2 and names the case.
4. Run with an explicit budget: `--n`, `--max-runs`, `--max-minutes`, and
   `--max-cost-usd`. Native calls spend the user's quota; run them only within
   the user's authorization.
5. Read `summary.cases.<id>.hosts.<host>` for each case. Quote verdicts with
   their pass counts and intervals from `interpretation`, and report invalid
   statuses separately from failures. When `underpowered` is true, report the
   result as undecided and pass on `recommendation`; more trials need the user's
   authorization.
6. Read a sample of traces under `run_dir/trials/` before you claim a behavior.

## Non-interactive setup

```sh
seatrial init --yes --skills plugins/my-plugin --host claude --json
seatrial new-case --skill my-skill --id my-skill-001 --summary "Asks before deleting files." --json
```

`init` refuses to replace an existing `seatrial.toml` without `--force`.
