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
| `[[mcp]]` | No | A stub MCP server with `name` and `responses`; see [stub MCP servers](#stub-mcp-servers) |

## Stub MCP servers

A skill that reads from or writes to an issue tracker, a wiki, or a chat service
through MCP needs those tools in the trial. Declare a stub server for each service:

```toml
[[mcp]]
name = "tracker"
responses = "tracker.json"
```

`name` starts with a lowercase letter and uses lowercase letters and digits joined by
single underscores. The agent
sees each tool as `mcp__<name>__<tool>` on both hosts. `responses` is a path inside
the case folder; a change to the file changes the case identity. A trial loads only
the declared stubs: Claude keeps `--strict-mcp-config` and allows `mcp__<name>`,
and Codex gets `-c mcp_servers.<name>` overrides with tool approval set to
`approve`.

The response file maps each tool name to its description, input schema, and
canned responses:

```json
{
  "get_issue": {
    "description": "Read one issue by its id.",
    "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    "responses": [
      {"match": {"id": "^42$"}, "result": {"id": "42", "title": "Login fails on Safari"}},
      {"result": "No such issue.", "is_error": true}
    ]
  },
  "add_comment": {"responses": [{"result": "Comment added."}]}
}
```

| Field | Required | Meaning |
|---|---|---|
| `description` | No | The tool description the agent sees |
| `input_schema` | No | A JSON Schema object with `"type": "object"`; default `{"type": "object"}` |
| `responses` | Yes | Canned responses; the first whose `match` fits the input answers the call |
| `responses[].match` | No | Input field to regular expression, searched in the field's text; a missing field does not fit; no `match` fits every input |
| `responses[].result` | Yes | A string becomes the text result; any other JSON value becomes JSON text, and an object is also the structured result |
| `responses[].is_error` | No | `true` returns the result as a tool error |

A call that no response fits, or a call to an undeclared tool, gets an error result.

The stub logs every call to `<trial>/mcp/<name>.jsonl`. The runner copies the logs
into evidence `mcp_calls` as `{"server", "tool", "input", "matched", "is_error"}`,
and adds `"server": <name>` to the stub's calls in `tools`. The `tools` helpers
work on stub calls:

```python
from seatrial import checks

def grade(project, evidence):
    return {
        'read_issue': checks.tool_used(evidence, 'mcp__tracker__get_issue', {'id': '^42$'}),
        'read_before_comment': checks.tool_order(evidence, 'mcp__tracker__get_issue',
                                                 'mcp__tracker__add_comment'),
    }
```

`mcp_calls` holds what reached the server, including unmatched calls
(`"matched": false`), so a grader can fail a trial that wrote before approval or
called a tool the fixture did not expect.

## Trigger modes

`trigger` decides whether the prompt names the skill. `--trigger` on `run` and
`compare` overrides it for every selected case and warns for each case whose mode
it changes: an `explicit` prompt names the skill, so `skill_triggered` and negative
cases stop measuring the description. NONE trials always send the request alone
and record `auto`.

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
result for it or raises. It passes when `evidence['skills']` shows the case skill.

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
edited files, skills loaded, final message, delegations, usage, cost, errors,
and `terminal`. The Claude and Codex adapters also return `tools` and
`tool_errors`.

`tools` lists every tool call in call order as `{"name": ..., "input": {...}}`,
with delegated sessions' calls where they ran. Calls to a
[stub MCP server](#stub-mcp-servers) also carry `"server"`. Names come from the host: Claude
reports `Bash`, `Read`, `Edit`, and `mcp__<server>__<tool>`; Codex reports
`shell`, `apply_patch`, `web_search`, its collaboration tools such as `wait`, and
`mcp__<server>__<tool>`.

Codex lists only completed calls, like `commands`, so a call cut off by a timeout
is in neither. Its stream reports a spawn only as started, so check delegation
with `delegated(evidence)`, not `spawn_agent`. Codex places a delegated call by
the time it completed, from the session rollouts; without the parent's rollout,
delegated calls follow the parent's calls.

`tool_errors` counts the tool calls in `tools` that the host marked as failed:
Claude tool results with `is_error`, and Codex items with status `failed`, such
as a command with a nonzero exit or an MCP call that returned an error.

When evidence has no `tools`, as on runs recorded before it existed or on a host
that cannot show tool calls, `tool_used`, `tool_order`, and `steps_at_most` fail
with `tool calls not recorded`. Rerun old trials before grading them with these
helpers.

The runner adds `source_changes`, `git_changed`, `git_changes` (the `.git` paths
that changed), `plugin_changed`, and `project_base`. `seatrial schema --json` prints the list.

`git_changed` compares the Git index by its entries, not its bytes: `git status`
and `git diff` rewrite the index to refresh file stat data, so a read-only command
does not count as a Git change, but staging does, and so does hiding a change with
`git update-index --assume-unchanged` or `--skip-worktree`. An empty
`.git/config.worktree`, `.git/objects/info/alternates`, or
`.git/objects/info/http-alternates` sets nothing and does not count either; Claude
Code's Bash sandbox on Linux creates them empty in every trial, before the agent
runs a command.

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
| `tool_used(evidence, name, matching=None)` | The agent called the tool; `name` may be a set such as `{"Bash", "shell"}`, and `matching` is a dict of input field to regular expression or a function of the input |
| `tool_order(evidence, first, then, first_matching=None, then_matching=None)` | Every `then` call comes after a `first` call, for example a read before a write; passes when no `then` call ran |
| `steps_at_most(evidence, n)` | The agent made at most `n` tool calls |
| `tool_calls(evidence, name, matching=None)` | The matching calls with their positions, for custom checks |
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

## Measure the noise floor

An A/A run compares a revision with itself, so every difference it shows is
noise. Run one when you add a case, change a grader, or change model or host
CLI version:

```sh
seatrial compare --base main --head main --case my-skill-001 --n 10
```

It costs as much as a comparison. seatrial labels the run `a/a`, reports each
case's drift (the absolute difference in pass rate between the two arms), and
marks a case `noisy` when identical arms still gave `improved` or `regressed`.
`seatrial report` lists noisy cases first. [Statistics](statistics.md#noise-floor-aa-runs)
has the math.

Use the result to choose `n` and assertion kinds:

| A/A result | What it means | What to do |
|---|---|---|
| `noisy` from a zero-tolerance assertion | The assertion fails now and then with no change; one flake reads as `regressed` | Fix the flake, or make the assertion a `rate` |
| `noisy` from a `rate` assertion | A chance result at most 5% of the time, or trials that share state | Run A/A again; if it repeats, look for shared state, rate limits, or a time-dependent grader |
| Primary drift as large as the change you want to detect | `n` is too small for that change | Raise `n`: the drift shrinks with √n, so four times the trials halve it |
| 0 drift at 100% or 0% in both arms | The case cannot vary, so it cannot show a gain | Keep it as a regression guard, or make it harder to measure gains |

Keep `zero` for assertions that never fail when the behavior is right, such as
`preserved`. An assertion that passes 93% of the time gives a false
`regressed` in about one A/A run in four at `n = 10`. Do not gate CI with
`--fail-on-regression` on a case an A/A run marked noisy.

To read a later comparison against the floor, pass the A/A run directory that
`seatrial runs` lists:

```sh
seatrial compare --base main --noise-floor RUN_DIR
seatrial report latest --noise-floor RUN_DIR --noise-floor OTHER_RUN_DIR
```

Each verdict then reads `within noise` or `beyond noise`: a change within the
floor is one the case made with nothing changed. The floor is advisory and
never changes a verdict.

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
