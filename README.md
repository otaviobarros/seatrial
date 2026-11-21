# seatrial

seatrial runs fixed evaluation cases for agent skills on agent CLIs (hosts),
grades each trial with deterministic code, and compares two revisions of your
skills. It uses only the Python 3.11+ standard library, Git, and the agent CLIs
you already have installed.

Built-in hosts: Claude Code (`claude -p`) and Codex CLI (`codex exec`). Add others
through a small adapter; see [hosts](docs/hosts.md).

## Install

Run it with [uv](https://docs.astral.sh/uv/). `uvx` builds the package into a cached,
isolated environment and runs the command; nothing is installed into your project.

```sh
uvx --from git+https://github.com/otaviobarros/seatrial seatrial --help  # from Git
uvx --from . seatrial --help                                            # from a checkout
uv tool install git+https://github.com/otaviobarros/seatrial            # keep `seatrial` on PATH
```

Without uv, use `pipx install git+https://github.com/otaviobarros/seatrial`, or run `python3 -m seatrial`
with `src/` on `PYTHONPATH`. The package has no dependencies beyond Python 3.11+.

seatrial runs on macOS and Linux. Windows is not supported; use WSL.

## Quickstart

```sh
cd my-skills-project
seatrial init            # detect skills, write seatrial.toml and an example case
seatrial doctor          # check Python, Git, the configuration, cases, and host CLIs
seatrial list
seatrial run --dry-run   # print the trial plan; no model calls
seatrial run --host claude --n 1
seatrial                 # interactive mode on a terminal
```

`init` finds folders that hold `<name>/SKILL.md`. Two layouts work:

- `plugin`: a Claude Code plugin root with `skills/` and `.claude-plugin/plugin.json`.
- `skills`: a plain folder of skills. seatrial mounts it as a generated plugin.

On a terminal, `init` asks which folder, namespace, case folder, and hosts to use.
With `--yes` or without a terminal, it uses the detected values. See
[configuration](docs/configuration.md) for every key.

## Commands

| Command | Does |
|---|---|
| `init` | Write `seatrial.toml` and a runnable example case |
| `doctor` | Check the environment; exits 3 when a check fails |
| `list` | List cases; accepts selectors |
| `new-case --skill S --id NAME-001` | Create a case skeleton; prompts on a terminal |
| `run [--rev REV]` | Run cases against one skill revision |
| `compare --base REV [--head REV]` | Run both revisions, interleaved, and give a verdict per case and host |
| `runs` | List retained run directories, newest first |
| `report [RUN_DIR\|latest]` | Print a run summary; `--format markdown` for a Markdown table |
| `regrade [RUN_DIR\|latest]` | Grade retained trials again with the current case code; no host calls |
| `hosts` | List host adapters, models, and CLI versions |
| `schema` | Print the configuration, case, evidence, and exit-code contract |
| `interactive` | Menu-driven mode; also the default with no arguments on a terminal |

Every command except `interactive` accepts `--json`. `seatrial COMMAND --help` lists all options.
For agents and CI, read [using seatrial from an agent](docs/agents.md).

## Run and compare

`run` and `compare` call host CLIs and spend quota. Before a native run:

1. Sign in to each CLI (`claude /login`, `codex login`).
2. Print the plan with `--dry-run`.
3. Set a budget: `--n`, `--max-runs`, `--max-minutes`, and `--max-cost-usd` for
   hosts that report cost.

A revision is a Git revision or `WORKTREE`, the skill files in the checkout with
uncommitted changes. `compare` defaults `--head` to `WORKTREE`, so
`seatrial compare --base main` measures uncommitted edits. Each revision mounts
as a whole skill folder; trials alternate between revisions so that time and load
affect both alike.

Selectors combine and repeat: `--case ID`, `--skill NAME`, `--rule ID`,
`--suite capability|regression`, `--host NAME`.

| Option | Default | Maximum |
|---|---|---|
| `--n` trials per case, host, and revision | `[run] n`, 3 | `[limits] max_n`, 10 |
| `--jobs` parallel trials | `[run] jobs`, 1 | `max_jobs`, 4 |
| `--max-runs` trials in the plan | `max_runs`, 100 | `max_runs` |
| `--max-minutes`; a trial starts only if its timeout fits | `max_minutes`, 90 | `max_minutes` |
| `--timeout` seconds per trial | case value, else `[run] timeout`, 180 | `max_timeout`, 600 |
| `--max-cost-usd` total host-reported cost | none | none |
| `--budget-usd` per-trial cap (Claude only) | none | none |

A usage-limit or authentication error stops the run. Finished trials are kept,
and the command exits 1.

## Verdicts

`compare` gives each case and host one advisory verdict:

| Verdict | Meaning |
|---|---|
| `regressed` | A zero-tolerance assertion newly fails, or an assertion is significantly worse (Fisher p < 0.05) |
| `improved` | The primary assertion is significantly better, and head has no zero-tolerance failure |
| `non-inferior` | No assertion drops by more than two trials' worth |
| `inconclusive` | Otherwise, or fewer than 80% of the trials in a revision are valid |

Ten trials per revision detect a change of about 50 percentage points on the
primary assertion. With 3 trials, `improved` is not possible. Report a
non-significant result as "no detected effect". The report also shows pass^k,
median lines added, mean wall time, and host-reported cost. Use
`--fail-on-regression` to exit 4 on any `regressed` verdict.

## Results

Each invocation writes a run directory under `[run] state_dir` (default
`$SEATRIAL_STATE`, else `~/.cache/workspaces/<timestamp>/`). The path prints at
the end of the run. Keep it out of version control. Projects can share a state
directory: `runs`, `report latest`, and `regrade` show only the current project's runs.

| File | Content |
|---|---|
| `run.json` | The run settings and revisions, written before the first trial |
| `records.jsonl` | One record per trial, written as each trial ends; `foreign_skills` and `outside_access` mark trials that used skills from outside the plugin or named the project path |
| `summary.json` | Per-case, per-host, per-revision summaries and verdicts; missing when the run was interrupted, until `seatrial regrade` builds `summary-regraded.json` |
| `trials/<random>/` | The trial project, `session.jsonl` trace, `stderr.txt`, `evidence.json`, `retained.json` |

Trial directories use random names so that no path the agent sees names the
evaluation. Private host state, including any copied login, is removed after each
trial.

Only `completed` trials are graded. Other statuses are excluded from pass rates:

| Status | Meaning |
|---|---|
| `control` | The grader marked the trial as a control, not a measurement |
| `trace_incomplete` | The agent delegated, but at least one delegated session was not captured |
| `usage_limit`, `auth_error` | Host quota or sign-in problem; the run stops |
| `timeout`, `host_stall` | The time cap ended the trial; `host_stall` means the machine paused |
| `incomplete` | The host exited without a successful final turn |
| `harness_error` | seatrial failed; the record holds the exception |
| `stale` | From `regrade` only: the retained files changed after the trial |

`regrade` writes `records-regraded.jsonl` and `summary-regraded.json`. Copy
retained trial files before you inspect them with tools that write.

## Documentation

- [Configuration](docs/configuration.md): `seatrial.toml` keys and defaults.
- [Writing cases](docs/cases.md): `case.toml`, `case.py`, evidence, and grading helpers.
- [Hosts](docs/hosts.md): built-in adapters and how to add one.
- [Using seatrial from an agent](docs/agents.md): JSON output, exit codes, and a safe procedure.

## License

MIT. See [LICENSE](LICENSE).
