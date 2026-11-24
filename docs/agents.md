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

## JSON output

With `--json`, stdout holds one JSON document. During `run` and `compare`, stderr
holds one JSON line per finished trial:

```json
{"event": "trial", "finished": 3, "scheduled": 8, "case_id": "demo-001", "host": "claude", "arm": "head", "trial_index": 1, "status": "completed", "pass": true, "wall_s": 41.2, "cost_usd": 0.03}
```

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
| `evidence` | `zero_tolerance`, `strong` (p < 0.01), `significant` (p < 0.05), `not_significant`, or `insufficient_trials`; under a correction, p is the adjusted p-value |
| `primary.base`, `primary.head` | `passes`, `valid`, `rate`, `ci` (95% Wilson `[low, high]`), `margin` (half the interval) |
| `primary.difference`, `primary.p_value` | Head rate minus base rate; Fisher p on the primary assertion |
| `primary.intervals_overlap` | `true` when the base and head intervals overlap |
| `deciding_assertion`, `deciding_p_value` | The assertion the reading rests on and its gate p-value: adjusted under a correction, else Fisher. For `inconclusive`, the assertion with the largest drop. With `zero_tolerance` evidence, the failing zero-tolerance assertion, and the p-value is `null` |
| `underpowered` | `true` when the difference is not significant and the run had fewer trials than `trials_needed` |
| `trials_needed` | Trials per revision to detect the observed change with 80% power, on the primary assertion or, for `inconclusive`, on `deciding_assertion`; `null` when there is no change, or with `zero_tolerance` evidence on an `inconclusive` result |
| `detectable_change` | The smallest change, as a fraction, that the run's trial count could detect in at least one direction, on the same assertion as `trials_needed`; `null` when no change up to 100 points could be detected |
| `alpha` | The per-test threshold behind `trials_needed`: 0.05, or 0.05 divided by `family_size` under a correction |
| `explanation`, `recommendation` | The same reading as text; the `markdown` and `comment` formats print these |

Every arm summary also has `primary_ci` and `assertion_ci` (`{assertion: [low, high]}`).
[Statistics](statistics.md) explains the method.

For a pull request comment, use `--format comment` on `run`, `compare`, or
`report`. Without `--json`, stdout holds only the Markdown; progress and the run
directory go to stderr. `--summary-file PATH` appends the same Markdown to PATH
with any format, with or without `--json`. [Continuous integration](ci.md) has
platform recipes.

`summary.multiple_comparisons` names the correction (`method`), the number of
Fisher tests it covered (`family_size`), and `alpha`. The family leaves out
tests whose pass counts cannot reach p < `alpha`. A decision with a `reason`,
such as a new zero-tolerance failure or too few valid trials, has no p-values.
Other decisions keep raw `p_values`; with a method other than `none` they add
`adjusted_p_values`, and `verdict_uncorrected` when the correction changed the
verdict. A tool should read these fields only when they are present. The `regressed`
list and exit code 4 use the corrected verdicts.

## Procedure

1. Run `seatrial doctor --json`. On exit 3, report each check whose `level` is
   `fail` and stop.
2. Select cases with `seatrial list --json` and the selectors the task names.
   Cases with `"holdout": true` run only with `--holdout`; run them once, after
   the skill edit is final, and report them apart from the tuning cases.
   A case whose `error` is not null cannot load its `case.py`; report the error
   and leave the case out.
   To measure whether a skill helps at all, compare with `--base NONE`. Text
   reports label that arm `none`; JSON keeps `base` and sets
   `summary.revisions.base.name` to `"NONE"`. To measure whether the agent picks
   a skill on its own, add `--trigger auto`.
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
