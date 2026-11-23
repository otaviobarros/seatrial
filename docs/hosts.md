# Hosts

A host adapter wraps one agent CLI, not one model.

| Host | CLI | Default model | Skill invocation |
|---|---|---|---|
| `claude` | Claude Code (`claude -p`) | `claude-haiku-5-5` | `/<namespace>:<skill>` |
| `codex` | Codex CLI (`codex exec`) | `gpt-6-luna` | `$<skill>` |

`seatrial hosts` lists every registered adapter with its model, efforts, and CLI
version. Set models in `[hosts] models` or with `--model HOST=MODEL`.

## Add a host

Adapters register from three sources, in order. A later source cannot replace a
name that an earlier source registered.

1. Built-in modules in `seatrial/hosts/`.
2. Modules named in `[hosts] modules` of `seatrial.toml`.
3. Installed distributions with a `seatrial.hosts` entry point:

   ```toml
   [project.entry-points."seatrial.hosts"]
   gemini = "my_package.gemini:ADAPTER"
   ```

The module defines `ADAPTER`, an instance of a `HostAdapter` subclass from
`seatrial.ports`, with `name` and `default_model` set. The runner calls, per
trial:

| Method | Returns |
|---|---|
| `version()` | The CLI version, or `None` when the CLI is unavailable |
| `invocation(skill)` | The prompt prefix that invokes the skill; not called for a `NONE` trial |
| `prepare(trial)` | Isolates the host and installs `trial.plugin`; returns the installed path, or `None` when `trial.plugin` is `None` |
| `command(trial)` | The argument list to launch |
| `environment(trial, base)` | Optional; the process environment |
| `collect(trial)` | An evidence dict with every key of `EVIDENCE_KEYS` |
| `release(trial, lock)` | Optional; removes private state such as a copied login |
| `classify(process, evidence, stderr)` | Optional; maps host errors to statuses such as `usage_limit` |

## Isolation

- Use no user configuration, user-level skills, MCP servers, or network for tool
  commands. Load skills only from `trial.plugin`. A `NONE` trial has
  `trial.plugin = None`: load no plugin. Set `supports_no_plugin = True` once
  `prepare` and `command` handle that; otherwise `NONE` is rejected for the host.
  The runner reports every skill a `NONE` trial loads as foreign and marks the
  trial `contaminated`.
- Build the environment from `HostAdapter.environment`. It passes only `PASS_ENV`
  (`PATH`, `HOME`, locale, proxy, and certificate variables), the adapter's
  `pass_env` patterns, and `[run] pass_env`, and gives Git only repository config and
  a fixed identity. Name the CLI's own variables, such as `OPENAI_*`, in `pass_env`.
- Override `foreign_skills(trial, evidence)` to return skills that did not come from
  the plugin, and `unknown_skills(trial, evidence)` to return skills whose source the
  trace cannot establish. The runner records them, and every command that names the
  project path, on the trial as `foreign_skills`, `unknown_skills`, and `outside_access`.
- Claude Code: with `ANTHROPIC_API_KEY` set, each trial gets its own
  `CLAUDE_CONFIG_DIR`. A subscription login lives in the user's config, so those
  trials keep it, and `doctor` warns when user-level skills exist. An explicit prompt
  expands the skill without a Skill call, so the adapter records the invoked skill
  in `skills` when the init event lists it in `slash_commands`.
- Codex CLI: its sandbox cannot deny reads, so a trial can read the evaluated
  project. Trials run outside it and receive no `PWD`; `outside_access` records any
  command that names its path.
- Give a host sandbox write access to the project, `.git` included, so that cases
  can grade attempted Git writes.
- Remove any copied login in `release`.

## Contract samples

Record a trace from a real run, remove personal paths, and save it as
`seatrial/hosts/samples/<name>/<sample>.jsonl` with a `<sample>.expected.json`
listing evidence values and the expected status. Optional keys: `foreign_skills`,
`unknown_skills`, `trial_project`, and `trial_plugin` (`null` replays a `NONE`
trial). `tests/test_host_contract.py` replays every sample.
