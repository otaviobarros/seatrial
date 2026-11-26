---
name: seatrial
description: Evaluate agent skills with seatrial. Use to set up skill evals, write a case, run cases on Claude Code or Codex, compare two skill revisions, or read seatrial results.
---

# seatrial

seatrial runs fixed cases against agent skills on agent CLIs (hosts), grades each
trial with code, and compares two revisions of the skills. The CLI is the source
of truth: `seatrial COMMAND --help` for options, `seatrial schema --json` for the
case, evidence, verdict, and exit-code contract.

## Invoke

Use `seatrial` when it is on PATH. Otherwise run
`uvx seatrial`. Pass `--json`
to every command and read the exit code:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The run stopped early: usage limit, sign-in, time, or cost cap |
| 2 | Usage error |
| 3 | Configuration error, or a failed `doctor` check |
| 4 | A `regressed` verdict with `--fail-on-regression` |
| 5 | `--changed REF` found no affected case; report that and run nothing |

Under `--json`, an error is `{"ok": false, "error": ..., "exit": N}` on stdout.
Report the `error` text and stop; a retry with the same arguments fails the same way.

## Set up

1. No `seatrial.toml` in the project root: run `seatrial init --yes --json`. Add
   `--skills PATH` and `--host claude|codex` when detection picks the wrong folder
   or host. `init` keeps an existing `seatrial.toml` unless the user asks for `--force`.
2. Run `seatrial doctor --json`. On exit 3, report each check whose `level` is
   `fail` and stop. Report each `warn` check with the result.

## Write a case

A case checks one behavior of one skill in a fictional project.

1. Run `seatrial new-case --skill SKILL --id NAME-001 --summary "ONE SENTENCE" --json`.
2. In `case.py`, `build(project)` writes the fixture project and returns the
   user's request in plain words. The host adds the skill invocation.
3. `grade(project, evidence)` returns `{assertion: (passed, note)}` for every
   assertion in `case.toml`. Grade observable effects: files, Git state, commands,
   and the final message in `evidence`. Use the helpers in `seatrial.checks`.
4. Mark an assertion `zero` when one failure is a regression; mark it `rate` when
   a pass rate is the measure. Name the main one in `primary`.
5. To evaluate a skill description, set `trigger = "auto"`, make
   `skill_triggered` the primary assertion, and add a negative case whose request
   must not load the skill. `seatrial schema --json` lists `triggers` and
   `built_in_assertions`.
6. Set `holdout = true` on cases the user wants kept out of tuning. Leave a
   held-out case unread and unrun while you edit the skill.
7. Check the case without model calls: `seatrial list --json` and
   `seatrial run --case ID --dry-run --json`, plus `--holdout` for a held-out case.

## Run and compare

Native runs call the host CLI and spend the user's quota. Run one only when the
user asked for it, with a budget the user set or approved.

1. Print the plan with `--dry-run --json`. Confirm that `count` fits the budget.
2. Run with explicit caps: `--n`, `--max-runs`, `--max-minutes`, and
   `--max-cost-usd`. Start with one case, one host, and `--n 1` to check the case
   itself.
3. Measure one revision with `seatrial run`. Measure a skill edit with
   `seatrial compare --base REV`; `--head` defaults to the uncommitted worktree.
   Measure whether a skill helps at all with `--base NONE`: no plugin, no invocation.
   Measure run-to-run noise with the same revision twice, `--base main --head main`;
   pass that run to later comparisons with `--noise-floor RUN_DIR`.
   Held-out cases run only with `--holdout`; run them once, after the edit is final.
   To run only the cases a change affects, add `--changed REF`, such as
   `--changed origin/main`.
   With many cases, add `--correction holm` (a gate) or `--correction bh` (a
   screen) so that false verdicts do not add up. To save trials, add
   `--sequential`; decided cases stop early.
4. Exit 1 means finished trials are kept. Report the reason; rerun only with the
   user's approval.

Size the trial count to the claim:

| Claim | Trials per revision, case, and host |
|---|---|
| The case runs and grades | 1 |
| A revision does not break a behavior | 5 |
| A revision improves a behavior | 10 |

Ten trials detect only changes of about 60 percentage points, and none from a
50% pass rate; 30 trials detect about 30 points. With 3 trials
`improved` is not possible. The summary says how many trials a smaller change
needs; see [statistics](https://github.com/otaviobarros/seatrial/blob/main/docs/statistics.md).

## Read results

1. Read `summary.cases.<id>.hosts.<host>` in the final JSON document.
2. Quote each verdict from `interpretation`: `verdict`, `evidence`, and the
   primary pass counts with their 95% intervals (`primary.base.ci`,
   `primary.head.ci`). Verdicts are `regressed`, `improved`, `non-inferior`, and
   `inconclusive`. A non-significant difference is "no detected effect", never an
   improvement.
3. When `underpowered` is true, say the run was too small to decide, and pass on
   `recommendation` with `trials_needed`. More trials spend quota: run them only
   within the user's authorization.
   Quote the change from `decision.effect_primary`: head minus base with its
   95% interval. A `cheaper` or `costlier` note in `decision.cost` is secondary
   and never changes the verdict.
   Then read `summary.skills.<skill>`: one pooled verdict per skill across its
   cases, from an exact stratified test on each primary assertion. Report it
   beside the case verdicts, never in place of them; it is not gated. When
   `directions` counts cases on both sides, say the cases disagree.
4. Report trials with a status other than `completed` apart from failures; they
   are not graded.
5. When `summary.label` is `a/a`, every difference is noise: report the cases in
   `summary.aa.noisy` as too unstable to gate on, not as changes. A pooled
   verdict other than `inconclusive` in an A/A run means the skill's cases
   drifted together; report it as noise too.
   When a comparison has `noise_floor` with `within` true, say the change is no
   larger than the case drifts with no change. When `noise_floor` is
   `{stale: true}`, the case changed since the A/A run: say the floor does not
   apply and suggest a new A/A run.
6. Report `isolation_warnings`: those trials loaded skills from outside the
   plugin or from an unknown source, or named the project path, so their results
   may not measure the skill.
7. Read a sample of `session.jsonl` traces under `run_dir/trials/` before you
   explain why a trial failed.
8. A grader fix needs no new host calls: `seatrial regrade latest --json`.
9. For a pull request comment or CI job summary, add `--format comment` to
   `run`, `compare`, or `report`, and `--summary-file PATH` to also append it
   to a file.
10. To see whether a case got better or worse over weeks, read
   `seatrial trend --json`. A row's `changes` lists what differs from the run
   before it: `skill_tree`, `grader_sha256`, `fixture_sha256`, `model`, or
   `cli_version`. Name that change when a pass rate moves right after it. Rows
   with `label` `a/a` measure noise, not a change.
