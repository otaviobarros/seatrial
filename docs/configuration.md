# Configuration

seatrial reads `seatrial.toml`. Commands search the working directory and its
parents; `--config PATH` names a file directly. Relative paths resolve against
the file's folder. `seatrial init` writes a starting file, and `seatrial
schema` lists the accepted keys. Unknown keys are errors.

```toml
[project]
name = "my-skills"

[skills]
path = "plugins/my-plugin"       # required
layout = "plugin"                # "plugin" or "skills"
namespace = "my-plugin"          # Claude invokes /my-plugin:<skill>

[cases]
path = "evals/cases"
hosts = ["codex", "claude"]      # default hosts for a case without `hosts`
python_path = ["evals/lib"]      # added to sys.path before case.py loads
identity_files = ["evals/lib/checks.py"]  # hashed into every case identity

[run]
n = 3
jobs = 1
timeout = 180
state_dir = "~/.cache/my-evals"  # optional run directory root
pass_env = ["MY_PROXY_TOKEN"]   # extra variables for agent CLIs; globs allowed

[limits]
max_n = 10
max_jobs = 4
max_runs = 100
max_minutes = 90
max_timeout = 600

[hosts]
modules = ["gemini_host"]  # adapter modules on python_path, such as evals/lib/gemini_host.py
models = { claude = "claude-haiku-5-5", codex = "gpt-6-luna" }
```

## Sections

| Key | Default | Meaning |
|---|---|---|
| `project.name` | folder name | Recorded in each run summary |
| `skills.path` | required | The folder under evaluation. `compare` mounts it from Git revisions |
| `skills.layout` | `plugin` | `plugin`: a root with `skills/<name>/SKILL.md` and `.claude-plugin/plugin.json`. `skills`: a folder of `<name>/SKILL.md`, mounted with a generated `plugin.json` |
| `skills.namespace` | `plugin.json` name, else the folder name | The Claude plugin namespace |
| `cases.path` | `evals/cases` | Case folders, `<skill>/<case-id>/` |
| `cases.hosts` | `["codex", "claude"]` | Hosts for cases that do not set `hosts` |
| `cases.python_path` | `[]` | Folders for shared grading modules |
| `cases.identity_files` | `[]` | Files whose change invalidates comparisons with old runs |
| `run.n`, `run.jobs`, `run.timeout` | 3, 1, 180 | Defaults for `--n`, `--jobs`, and trial seconds |
| `run.effort` | none | Effort for every case, like `--effort`; unset keeps each case's effort |
| `run.pass_env` | `[]` | Extra environment variables for agent CLIs, by name or glob such as `MY_*`; see [isolation](hosts.md#isolation) |
| `run.state_dir` | `$SEATRIAL_STATE`, else `$XDG_CACHE_HOME/workspaces` or `~/.cache/workspaces` | Run directories |
| `limits.*` | see the example | Caps that options cannot exceed; positive integers |
| `hosts.modules` | `[]` | Modules that define an `ADAPTER`; see [hosts](hosts.md) |
| `hosts.models` | adapter default | Model per host; `--model HOST=MODEL` overrides it |

Each key has one type: a string, a positive integer, a list of strings, or, for
`hosts.models`, a table of strings. A value of another type is a configuration error.

Keep `state_dir` outside the project. Run directories hold agent traces and
fictional projects, not source.

## Revisions

`WORKTREE` works in any folder. A Git revision needs the skills folder inside a
Git checkout; `doctor` reports whether `compare` can mount revisions.
