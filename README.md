<!-- markdownlint-disable-next-line MD041 -->
<p align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
  <img alt="seatrial" src="docs/images/logo-light.png" width="420">
</picture>
</p>

<p align="center">Sea trials for agent skills.</p>

<p align="center">
  <a href="https://github.com/otaviobarros/seatrial/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/otaviobarros/seatrial/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-1d6fa5">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-0b7a8f">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-0b2540"></a>
</p>

You changed a skill. Did the agent get better, or did it only feel better on the
one prompt you tried? seatrial answers that the way a shipyard tests a boat: it
takes the skill out on a fixed course, many times, and records what happened.

- **Real agents.** Each trial runs the actual CLI, Claude Code (`claude -p`) or
  Codex (`codex exec`), in a fresh project. Add other hosts through a small
  [adapter](docs/hosts.md).
- **Graded by code.** A case's `grade()` checks files, Git state, commands, and the
  final message. No model judges the result.
- **Two revisions, one verdict.** `compare` runs `main` against your uncommitted
  edits and reports `improved`, `non-inferior`, `regressed`, or `inconclusive`.
- **Nothing to install into your project.** Python 3.11+ standard library and Git.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/how-it-works-dark.svg">
  <img alt="Diagram of one trial in five steps: case, mount, agent, evidence, grade. The trial repeats n times per case, host, and revision, and the results go to summary.json." src="docs/images/how-it-works-light.svg">
</picture>

<details>
<summary>The five steps in text</summary>

Each trial runs these steps:

1. **Case.** `build()` in the case's `case.py` writes a small fictional project
   and returns the request a user would type.
2. **Mount.** seatrial mounts your skills at one revision, a Git ref such as
   `main` or the uncommitted `WORKTREE`, as a plugin.
3. **Agent.** The real host CLI (`claude` or `codex`) runs the request in a
   fresh, isolated folder.
4. **Evidence.** seatrial records the commands, file edits, loaded skills, and
   final message in `evidence.json`.
5. **Grade.** `grade()` checks that evidence and the project, and returns pass or
   fail for each assertion in `case.toml`.

</details>

seatrial repeats each trial n times per case, host, and revision, and writes the
pass rates and verdicts to `summary.json`. Repetition turns one lucky run into a
pass rate.

## How seatrial compares

Other tools evaluate agents too. This table reflects their public documentation
in November 2026.

| | seatrial | [`claude plugin eval`](https://code.claude.com/docs/en/plugin-evals) | [promptfoo](https://www.promptfoo.dev/docs/guides/test-agent-skills/) | [Inspect](https://inspect.aisi.org.uk) + [inspect_swe](https://meridianlabs-ai.github.io/inspect_swe) |
|---|---|---|---|---|
| Agents | Claude Code and Codex CLIs; others through an adapter | Claude Code | Claude Agent SDK, Codex SDK, OpenCode SDK | Claude Code and Codex CLI as Inspect agents, with any model |
| Compares | Two Git revisions of your skills, including uncommitted edits | The plugin loaded against no plugin | Skill versions in separate fixture folders, side by side | Models and agents on a task set |
| Grading | Your Python code over files, Git state, commands, and the final message | Regex, tool use, and file checks, plus model-judged rubrics | Assertions such as skill used, custom JavaScript, plus model-graded | Code or model scorers |
| Statistics | Fisher exact test per assertion, and a verdict per case and host | Three runs per case by default, mean scores against a threshold | `--repeat`; no significance test | Metrics such as accuracy and standard error |
| Isolation | Fresh Git project per trial, filtered environment, flags skills from outside the plugin | Fresh home, working folder, and config per run | A `working_dir` per provider | Docker sandbox |
| Runtime | Python 3.11+ standard library; no dependencies | Built into Claude Code 2.1.269+ | Node and the agent SDK packages | Python packages; Docker for the sandbox |

Choose seatrial to answer "did this skill edit make the agent better or worse"
on the real CLIs, with code-graded cases and a statistical verdict. Choose
`claude plugin eval` to check that a plugin helps compared with no plugin, inside
Claude Code. Choose promptfoo when your evals already live in promptfoo or need
model-graded rubrics. Choose Inspect for benchmark-scale runs in sandboxes or
across many models.

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

## Agent skill

[`skills/seatrial`](skills/seatrial/SKILL.md) teaches an agent to set up seatrial,
write cases, run and compare within a budget, and read the results. Install it with
the [skills](https://skills.sh) CLI:

```sh
npx skills add otaviobarros/seatrial
```

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

<details>
<summary>Options, defaults, and limits</summary>

| Option | Default | Maximum |
|---|---|---|
| `--n` trials per case, host, and revision | `[run] n`, 3 | `[limits] max_n`, 10 |
| `--jobs` parallel trials | `[run] jobs`, 1 | `max_jobs`, 4 |
| `--max-runs` trials in the plan | `max_runs`, 100 | `max_runs` |
| `--max-minutes`; a trial starts only if its timeout fits | `max_minutes`, 90 | `max_minutes` |
| `--timeout` seconds per trial | case value, else `[run] timeout`, 180 | `max_timeout`, 600 |
| `--max-cost-usd` total host-reported cost | none | none |
| `--budget-usd` per-trial cap (Claude only) | none | none |

</details>

A usage-limit or authentication error stops the run. Finished trials are kept,
and the command exits 1.

## Verdicts

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/compare-dark.svg">
  <img alt="compare: base main passes 4 of 10 trials, head WORKTREE passes 10 of 10, verdict improved with Fisher p = 0.011" src="docs/images/compare-light.svg">
</picture>

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

<p align="center">
  <img alt="Terminal output of seatrial compare: progress lines, then a table of valid trials, pass counts, pass^k, wall time, cost, and verdicts for two cases on Claude and Codex" src="docs/images/terminal.svg" width="880">
</p>

## Results

Each invocation writes a run directory under `[run] state_dir` (default
`$SEATRIAL_STATE`, else `~/.cache/workspaces/<timestamp>/`). The path prints at
the end of the run. Keep it out of version control. Projects can share a state
directory: `runs`, `report latest`, and `regrade` show only the current project's runs.

<details>
<summary>Files in a run directory</summary>

| File | Content |
|---|---|
| `run.json` | The run settings and revisions, written before the first trial |
| `records.jsonl` | One record per trial, written as each trial ends; `foreign_skills` and `outside_access` mark trials that used skills from outside the plugin or named the project path |
| `summary.json` | Per-case, per-host, per-revision summaries and verdicts; missing when the run was interrupted, until `seatrial regrade` builds `summary-regraded.json` |
| `trials/<random>/` | The trial project, `session.jsonl` trace, `stderr.txt`, `evidence.json`, `retained.json` |

</details>

Trial directories use random names so that no path the agent sees names the
evaluation. Private host state, including any copied login, is removed after each
trial.

<details>
<summary>Trial statuses</summary>

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

</details>

`regrade` writes `records-regraded.jsonl` and `summary-regraded.json`. Copy
retained trial files before you inspect them with tools that write.

## Documentation

- [Configuration](docs/configuration.md): `seatrial.toml` keys and defaults.
- [Writing cases](docs/cases.md): `case.toml`, `case.py`, evidence, and grading helpers.
- [Hosts](docs/hosts.md): built-in adapters and how to add one.
- [Using seatrial from an agent](docs/agents.md): JSON output, exit codes, and a safe procedure.

## License

MIT. See [LICENSE](LICENSE).
