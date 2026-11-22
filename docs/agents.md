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
`stopped_early`, `regressed` (a list of `"<case> <host>"`), and `summary`.
`seatrial schema --json` prints the case, evidence, status, verdict, and
exit-code contract.

## Procedure

1. Run `seatrial doctor --json`. On exit 3, report each check whose `level` is
   `fail` and stop.
2. Select cases with `seatrial list --json` and the selectors the task names.
   Cases with `"holdout": true` run only with `--holdout`; run them once, after
   the skill edit is final, and report them apart from the tuning cases.
3. Print the plan with `--dry-run --json`. Confirm that `count` fits the task's
   budget before any native call.
4. Run with an explicit budget: `--n`, `--max-runs`, `--max-minutes`, and
   `--max-cost-usd`. Native calls spend the user's quota; run them only within
   the user's authorization.
5. Read `summary.cases.<id>.hosts.<host>` for each case. Quote verdicts with
   their trial counts, and report invalid statuses separately from failures.
6. Read a sample of traces under `run_dir/trials/` before you claim a behavior.

## Non-interactive setup

```sh
seatrial init --yes --skills plugins/my-plugin --host claude --json
seatrial new-case --skill my-skill --id my-skill-001 --summary "Asks before deleting files." --json
```

`init` refuses to replace an existing `seatrial.toml` without `--force`.
