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
| `skill` | Yes | The parent folder name; the skill the case evaluates |
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
| `trigger` | No | `explicit` (default) or `auto`; see [trigger modes](#trigger-modes) |

## Trigger modes

`trigger` decides whether the prompt names the skill. `--trigger` on `run` and
`compare` overrides it for every selected case.

| Mode | Prompt | Measures |
|---|---|---|
| `explicit` | The host invocation, such as `/plan-change`, then the request | What the skill does once it runs |
| `auto` | The request alone; the plugin stays mounted | Whether the agent picks the skill from its description |

Use `explicit` for cases about the skill's procedure and output. Use `auto` to
evaluate a description edit: the description is the only thing that makes the
agent load the skill.

In `auto` cases, declare `skill_triggered` as an assertion, usually the primary:

```toml
trigger = "auto"
primary = "skill_triggered"
assertions = { skill_triggered = "rate", behavior = "rate" }
```

seatrial grades `skill_triggered` from the trace when `grade()` returns no
result for it. It passes when `evidence['skills']` shows the case skill.

Add a negative case for every description edit: a request close to the skill's
domain that the skill must not trigger on. Its grader asserts the opposite:

```python
from seatrial.checks import skill_triggered


def grade(project, evidence):
    triggered, note = skill_triggered(evidence, CASE.skill)
    return {'quiet': (not triggered, note)}
```

When a trial loads another skill from the plugin, its record lists it under
`other_skills`. A rise in `other_skills` means the description now competes with
a sibling skill.

## Held-out cases

A held-out case is a case you keep out of the edit loop. You tune the skill
against your other cases, then run the held-out ones once, after the edit is
final. They answer a question the tuning cases cannot: does the edit help on
prompts you did not write it for?

### When to use them

- You edit a skill over several rounds until its cases pass.
- The skill must handle inputs beyond the cases you have, such as other change
  types, other file layouts, or other phrasings.
- You report a before-and-after result to someone else.

A single smoke case or a regression guard does not need a held-out partner.

### Benefits

- **Catches overfitting.** An edit can teach the agent the exact prompts you
  tuned on and break the neighbors. Tuning cases cannot show that, because the
  edit was shaped to pass them.
- **Keeps you honest by default.** `run` and `compare` skip held-out cases, so
  they cannot leak into a tuning round by accident. Naming one with `--case`
  without `--holdout` is a usage error, and `--holdout --case` on a tuning case
  is one too.
- **Stays visible.** `list` shows every case and marks held-out ones. Run
  summaries carry `holdout` per case, so a report can separate the two sets.

### Example

A `changelog` skill has two tuning cases, a feature and a fix, and two
held-out cases, a removal and an entry added to an existing `## Unreleased`
section. Mark the held-out ones in `case.toml`:

```toml
id = "changelog-removal-001"
version = 1
skill = "changelog"
suite = "capability"
summary = "A removal becomes a Removed entry under Unreleased."
primary = "entry"
holdout = true
assertions = { entry = "rate", preserved = "zero" }
```

Edit the skill and compare on the tuning cases as often as you need. When the
edit is final, measure the held-out cases once:

```sh
seatrial compare --base main               # tuning cases, every round
seatrial compare --base main --holdout     # held-out cases, once at the end
```

In one such run, an edit that listed only `Added` and `Fixed` entries scored
10/10 before and after on the tuning cases. On the held-out cases it dropped
from 10/10 to 8/10 and 9/10: the agent filed a removal as
`- Fixed: removed deprecated --legacy flag (#58)`.

A held-out case that you read, debug, or change while you edit the skill stops
being held out. Turn it into a tuning case by removing `holdout = true`, and
write a new held-out case in its place.

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
| `skill_triggered(evidence, skill)` | The trace shows the skill as `plugin:skill`, the bare name, or its `SKILL.md` path; on Claude, a prompt that starts with `/<skill>` counts when the CLI lists it as a slash command |
| `skill_loaded(evidence, skill)` | The same check; the runner flags skills from outside the plugin |
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
