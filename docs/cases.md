# Writing cases

A case is a folder. To evaluate a new skill or rule, add a case; seatrial needs
no code change.

```sh
seatrial new-case --skill plan-change --id plan-scope-001
```

```text
<cases>/plan-change/plan-scope-001/
  case.toml     metadata
  case.py       build() and grade()
  fixture/      files that build() copies into the project
```

Folders whose names start with `_` (for example `<cases>/<skill>/_shared/`) hold
shared modules and fixtures. They are never discovered as cases.

## case.toml

| Key | Required | Meaning |
|---|---|---|
| `id` | Yes | The folder name, in the form `name-001` |
| `version` | Yes | Increase it when the prompt, fixture, or grader changes meaning |
| `skill` | Yes | The parent folder name; the host invokes this skill |
| `suite` | Yes | `capability` (expected to improve) or `regression` (must not break) |
| `summary` | Yes | One sentence on the behavior the case checks |
| `primary` | Yes | The assertion that `improved` is measured on |
| `assertions` | Yes | `{ id = "rate" }` compares a pass rate; `{ id = "zero" }` makes any new failure a regression |
| `rules` | No | Rule IDs from the skill, for `--rule` selection |
| `hosts` | No | Default `[cases] hosts` |
| `effort` | No | Default `low` |
| `project` | No | The project folder name the agent sees; default `project` |
| `timeout` | No | Seconds per trial |
| `shared` | No | Paths to shared modules or fixture folders inside the case root |
| `holdout` | No | `true` keeps the case out of tuning; see [held-out cases](#held-out-cases) |

## Held-out cases

A skill tuned until its own cases pass can overfit those cases. Mark a few cases
`holdout = true` and leave them alone while you edit the skill. `run` and
`compare` skip them, and naming one with `--case` is a usage error. When the edit
is done, measure them once:

```sh
seatrial compare --base main --holdout
```

`list` shows every case and marks held-out ones. Summaries carry `holdout` per
case. A held-out case that you change or debug against stops being held out;
replace it with a new one.

## case.py

- `build(project)` creates the fictional project and returns the request text.
  The host adapter adds the skill invocation; a prompt that starts with `/` or `$`
  is rejected.
- `grade(project, evidence)` returns `{assertion: (passed, note)}` for every
  assertion in `case.toml`. Return `{"_status": "control", "_note": "..."}` to mark
  a trial as a control.

Before `case.py` runs, the loader sets `CASE`, `ID`, `FIXTURE` (the fixture
folder), and `SHARED` (`{file stem: module or folder}`).

`evidence` holds the keys of `EVIDENCE_KEYS` in `seatrial/ports.py`: commands,
edited files, skills loaded, final message, delegations, usage, cost, errors, and
`terminal`. The runner adds `source_changes`, `git_changed`, `plugin_changed`,
and `project_base`. `seatrial schema --json` prints the list.

## Grading helpers

Import from `seatrial.checks`:

| Helper | Use |
|---|---|
| `init_repo`, `copy_fixture`, `commit_all` | Build a Git project; `copy_fixture` installs `gitignore` as `.gitignore` |
| `preserved(evidence, allow=[...])` | Only the allowed files changed; Git and the plugin are untouched |
| `skill_loaded(evidence, skill)` | The host loaded the skill from the mounted revision |
| `delegated(evidence)` | The agent delegated and the delegated sessions were captured |
| `ran_tests`, `tests_ran`, `no_tests_ran`, `probe_runs` | Inspect the commands the agent ran |
| `report_matches(evidence, pattern)` | Search the final message |
| `run`, `clone` | Run the project's checks in a copy, so grading never changes the candidate |
| `line_counts(project, base)` | Lines added and removed since the base commit |

Put project-specific helpers in a folder named in `[cases] python_path`, and list
the files in `[cases] identity_files`.

## Rules for case authors

- Keep prompts, file names, and fixture text free of evaluation vocabulary, so
  the agent cannot tell that it is being evaluated.
- Grade effects the agent cannot fake: run real checks in a copy and read the
  project files. The agent's report alone is not evidence.
- Write a grader test with a passing and a failing candidate.
- Run one native trial on the cheapest model and read the trace before you rely
  on the case.

## Scope and preservation

Each prompt defines the scope before the agent starts. The runner records
manifests before and after each trial:

| Resource | Rule |
|---|---|
| Approved source | Edits only in the paths the prompt names; list them in `preserved(..., allow=[...])` |
| Protected source | Every other file keeps its hash; a symlink cannot replace a file |
| Git | The `.git` manifest stays the same unless the case authorizes Git writes |
| Plugin | The mounted skills stay the same |
| Scratch | `.scratch/` is disposable; temporary and cache variables point there |
| Python caches | `__pycache__` and `.pyc` files are not compared |

## Inspect the trace

A grader pass is necessary, not sufficient. Read a sample of
`trials/<random>/session.jsonl` before you report a result:

- The skill loaded from the mounted revision, not from a user installation.
- Reviewer results came from a delegated session, not from a self-reported pass.
- Denied actions were not attempted. A denied attempt is still an attempted violation.
- An exit status of zero can hide an earlier failed command.

Classify each failure as tooling or fixture, host or environment, agent behavior,
or unclear skill guidance. Keep the skill files fixed while you repair a grader.
