"""The host port: what the runner needs from an agent CLI, and what it gets back.

An adapter wraps one agent CLI (Codex, Claude Code, ...), not one model. The runner
calls the methods in this order for every trial:

  version()                     once per run; None means the CLI is unavailable
  invocation(skill)             the prompt prefix that invokes a plugin skill
  prepare(trial)                isolate the host and install the plugin
  command(trial), environment(trial, base)
  ... the runner launches the command and waits ...
  collect(trial)                parse the traces into an Evidence dict
  foreign_skills(trial, evidence)  skills that did not come from the plugin
  unknown_skills(trial, evidence)  skills whose source the trace cannot establish
  release(trial, lock)          remove private state; never keep a copied login

To add a host, define an `ADAPTER` object that subclasses `HostAdapter` in a module
named under `[hosts] modules` in seatrial.toml, or in a distribution's
`seatrial.hosts` entry point. Built-in adapters live in `seatrial/hosts/<name>.py`
with recorded traces under `seatrial/hosts/samples/<name>/`; the contract tests
(`tests/test_host_contract.py`) replay them. See docs/hosts.md.
"""
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re

from seatrial import config


# Keys every adapter's collect() returns. Case graders may read all of them.
EVIDENCE_KEYS = {
    'commands': list,         # shell commands, parent and delegated sessions, as strings
    'file_changes': list,     # paths the agent's edit tools touched, as reported by the host
    'skills': list,           # skill files read or skill tools invoked, as reported by the host
    'final_message': (str, type(None)),  # the last top-level report to the user
    'delegations': int,       # subagent or reviewer launches
    'delegation_traces': int,  # delegated sessions whose actions are included above
    'usage': (dict, type(None)),  # token counts as the host reports them
    'cost_usd': (float, int, type(None)),  # host-reported cost, when the host reports one
    'errors': list,           # host error events, truncated
    'terminal': bool,         # the host reported a completed final turn
}


def new_evidence():
    return {'commands': [], 'file_changes': [], 'skills': [], 'final_message': None,
            'delegations': 0, 'delegation_traces': 0, 'usage': None, 'cost_usd': None,
            'errors': [], 'terminal': False}


def validate_evidence(evidence):
    """Return a list of contract problems; an empty list means the evidence is valid."""
    problems = [f'missing {key}' for key in EVIDENCE_KEYS if key not in evidence]
    for key, kind in EVIDENCE_KEYS.items():
        if key in evidence and not isinstance(evidence[key], kind):
            problems.append(f'{key} has type {type(evidence[key]).__name__}')
    for key in ('commands', 'file_changes', 'skills', 'errors'):
        if isinstance(evidence.get(key), list) and not all(isinstance(v, str) for v in evidence[key]):
            problems.append(f'{key} must hold strings')
    return problems


@dataclass
class Trial:
    """Paths and settings for one trial. The runner creates it; adapters read it."""
    directory: Path          # private to this trial; holds traces and host state
    project: Path            # the agent's working directory
    plugin: Path             # the mounted plugin revision (read-only source)
    prompt: str
    model: str
    effort: str
    timeout: int
    budget_usd: float | None = None   # per-trial spend cap for hosts that support one
    state: dict = field(default_factory=dict)  # adapter-private values

    @property
    def trace(self):
        return self.directory / 'session.jsonl'

    @property
    def stderr(self):
        return self.directory / 'stderr.txt'


# Error text that means "stop the whole run", shared by hosts unless they override it.
USAGE_LIMIT = re.compile(r'usage limit|rate limit|quota|too many requests|\b429\b|purchase more credits|'
                         r'credit balance is too low', re.I)
AUTH_ERROR = re.compile(r'\b401\b|unauthori[sz]ed|not logged in|login required|please run /login|'
                        r'invalid api key|token (has )?expired', re.I)


# Variables every agent CLI receives. Anything else in the caller's environment, such as
# PWD, tokens, and tool settings, stays out of the trial unless an adapter or
# `[run] pass_env` names it.
PASS_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM', 'TZ', 'LANG', 'LANGUAGE', 'LC_*',
            'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
            'no_proxy', 'all_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS',
            # Version managers, so CLIs installed through shims still start.
            'MISE_*', 'ASDF_*', 'NVM_DIR', 'VOLTA_HOME', 'PNPM_HOME', 'BUN_INSTALL')


def filter_environment(base, patterns):
    """Keep only the variables whose names match one of the glob patterns."""
    return {key: value for key, value in base.items() if any(fnmatchcase(key, p) for p in patterns)}


# The fixed Git identity for trial repositories and the commands that run in them.
GIT_IDENTITY = ('Dana Reyes', 'dana@example.invalid')


def git_environment(base):
    """Return base without inherited Git settings, with only repository config and a fixed identity.

    User and system config never load, so signing programs, hooks, aliases, and
    credential helpers of the calling user cannot change a trial's Git results."""
    env = {key: value for key, value in base.items() if not key.startswith('GIT_')}
    name, email = GIT_IDENTITY
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
               GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
               GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    return env


class HostAdapter:
    """Base class for host adapters. Subclasses must set `name` and `default_model`."""
    name = None
    default_model = None
    efforts = ('low', 'medium', 'high')
    pass_env = ()  # extra variable patterns the CLI needs, such as its API key
    usage_limit = USAGE_LIMIT
    auth_error = AUTH_ERROR

    def version(self):
        raise NotImplementedError

    def invocation(self, skill):
        raise NotImplementedError

    def prepare(self, trial):
        """Create private host state and install the plugin. Return the installed plugin path."""
        raise NotImplementedError

    def command(self, trial):
        raise NotImplementedError

    def environment(self, trial, base):
        active = config.active()
        extra = tuple(active.pass_env) if active else ()
        env = git_environment(filter_environment(base, PASS_ENV + tuple(self.pass_env) + extra))
        scratch = str(trial.project / '.scratch')
        for key in ('TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME'):
            env[key] = scratch
        return env

    def collect(self, trial):
        raise NotImplementedError

    def foreign_skills(self, trial, evidence):
        """Skills in the evidence that did not come from the mounted plugin."""
        return []

    def unknown_skills(self, trial, evidence):
        """Skills in the evidence whose source the trace cannot establish."""
        return []

    def release(self, trial, lock):
        """Remove private state. Return audit fields for the trial record."""
        return {}

    def pin(self, model, effort):
        """Hash the launch flags without per-trial paths or prompts."""
        probe = Trial(Path('/trial'), Path('/project'), Path('/plugin'), '', model, effort, 1)
        flags = [a for a in self.command(probe)[:-1]]
        return hashlib.sha256(json.dumps(flags).encode()).hexdigest()

    def classify(self, process, evidence, stderr):
        """Map a finished process and its evidence to a trial status."""
        text = ' '.join(evidence['errors']) + ' ' + stderr
        if self.usage_limit.search(text) and not evidence['terminal']:
            return 'usage_limit'
        if self.auth_error.search(text) and not evidence['terminal']:
            return 'auth_error'
        if 'timeout' in process:
            return 'host_stall' if process.get('stalled_s') else 'timeout'
        if process.get('exit') == 0 and evidence['terminal'] and not evidence['errors']:
            if evidence['delegation_traces'] < evidence['delegations']:  # Every delegate must be traced.
                return 'trace_incomplete'
            return 'completed'
        return 'incomplete'
