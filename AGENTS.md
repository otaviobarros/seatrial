# seatrial

Keep the package dependency-free: import only the standard library and
`seatrial` modules, and read project facts from the active configuration
(`config.current()`). Project-specific checks belong to the consuming project,
behind `[cases] python_path`; tests never rely on a configuration outside their
disposable project.

- Run the tests from the repository root: `PYTHONPATH=src python3 -B -m unittest discover -s tests -t .`.
  Tests build disposable projects with `tests/support.py`; they make no model calls.
- Read [hosts](docs/hosts.md) before changing an adapter or the `HostAdapter` port,
  and add a recorded sample for every adapter behavior change.
- Keep `cli.py` output and exit codes in step with [agents](docs/agents.md) and
  `command_schema`; agents parse them.
- Keep the command table in [README.md](README.md) in step with `build_parser`.
