"""seatrial: run evaluation cases for agent skills on agent CLIs and compare revisions.

Modules:
  ports     the HostAdapter port and the Evidence record every adapter returns
  hosts     one adapter per agent CLI, registered by name
  cases     folder-based case discovery and selection
  checks    shared fixture helpers and grading checks for case authors
  process   bounded process launch
  workspace skill mounts, file manifests, and the run state directory
  stats     pass@k, pass^k, Fisher and Mann-Whitney tests, verdicts
  report    grading, per-arm summaries, and the Markdown report
  runner    scheduling and execution of trials
  config    seatrial.toml discovery, validation, and the active configuration
  setup     `init` and `doctor`
  cli       the seatrial command
  interactive  the menu-driven terminal mode
"""

__version__ = '0.1.0'  # x-release-please-version
