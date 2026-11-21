"""Project configuration: where the skills, cases, and run state live.

A project declares itself with `seatrial.toml`. The CLI finds the nearest one at or
above the working directory and activates it; library code reads `current()`.
Every path in the file is relative to the file's directory.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import tomllib

FILENAME = 'seatrial.toml'
LAYOUTS = ('plugin', 'skills')
LIMITS = {'max_n': 10, 'max_jobs': 4, 'max_runs': 100, 'max_minutes': 90, 'max_timeout': 600}
DEFAULTS = {'n': 3, 'jobs': 1, 'timeout': 180, 'effort': None}
SECTIONS = {'project': {'name'},
            'skills': {'path', 'layout', 'namespace'},
            'cases': {'path', 'python_path', 'identity_files', 'hosts'},
            'run': {'state_dir', *DEFAULTS},
            'limits': set(LIMITS),
            'hosts': {'modules', 'models'}}


class ConfigError(ValueError):
    """The configuration is missing or invalid."""


@dataclass
class Config:
    root: Path
    skills: Path
    cases: Path
    file: Path | None = None
    name: str = 'project'
    layout: str = 'plugin'
    namespace: str | None = None
    python_path: list = field(default_factory=list)
    identity_files: list = field(default_factory=list)
    hosts: list = field(default_factory=lambda: ['codex', 'claude'])
    host_modules: list = field(default_factory=list)
    models: dict = field(default_factory=dict)
    state: Path = None
    defaults: dict = field(default_factory=lambda: dict(DEFAULTS))
    limits: dict = field(default_factory=lambda: dict(LIMITS))

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        if self.state is None:
            self.state = default_state()
        if self.namespace is None:
            self.namespace = plugin_name(self.skills, self.layout) or self.skills.name

    def as_dict(self):
        """A JSON-safe view, for `doctor --json` and agents."""
        return {'file': str(self.file) if self.file else None, 'root': str(self.root), 'name': self.name,
                'skills': {'path': str(self.skills), 'layout': self.layout, 'namespace': self.namespace},
                'cases': {'path': str(self.cases), 'python_path': [str(p) for p in self.python_path],
                          'identity_files': [str(p) for p in self.identity_files], 'hosts': self.hosts},
                'run': {'state_dir': str(self.state), **self.defaults}, 'limits': self.limits,
                'hosts': {'modules': self.host_modules, 'models': self.models}}


def default_state():
    """Run directories live outside the project. Agent-visible names stay neutral."""
    override = os.environ.get('SEATRIAL_STATE')
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'workspaces'


def plugin_name(skills, layout):
    """The plugin name from `.claude-plugin/plugin.json`, when the layout is a plugin."""
    manifest = Path(skills) / '.claude-plugin' / 'plugin.json'
    if layout != 'plugin' or not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text()).get('name')
    except ValueError:
        return None


def find(start=None):
    """The nearest seatrial.toml at or above start, or None."""
    here = Path(start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    return None


def load(path):
    """Read and validate one configuration file."""
    path = Path(path).resolve()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f'{path}: {error}') from error
    return from_dict(data, path.parent, path)


def from_dict(data, root, file=None):
    root = Path(root).resolve()
    where = file or root
    unknown = [s for s in data if s not in SECTIONS]
    for section, keys in SECTIONS.items():
        if not isinstance(data.get(section, {}), dict):
            raise ConfigError(f'{where}: [{section}] must be a table')
        unknown += [f'{section}.{k}' for k in data.get(section, {}) if k not in keys]
    if unknown:
        raise ConfigError(f'{where}: unknown keys {sorted(unknown)}')
    project, skills, cases = data.get('project', {}), data.get('skills', {}), data.get('cases', {})
    run, hosts = data.get('run', {}), data.get('hosts', {})
    if 'path' not in skills:
        raise ConfigError(f'{where}: [skills] path is required')
    layout = skills.get('layout', 'plugin')
    if layout not in LAYOUTS:
        raise ConfigError(f'{where}: skills.layout must be one of {LAYOUTS}')
    resolve = lambda value: (root / Path(value).expanduser()).resolve()
    limits = dict(LIMITS, **data.get('limits', {}))
    defaults = dict(DEFAULTS, **{k: v for k, v in run.items() if k in DEFAULTS})
    for key, value in limits.items():
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f'{where}: limits.{key} must be a positive integer')
    config = Config(root=root, file=file, name=project.get('name', root.name),
                    skills=resolve(skills['path']), layout=layout, namespace=skills.get('namespace'),
                    cases=resolve(cases.get('path', 'evals/cases')),
                    python_path=[resolve(p) for p in cases.get('python_path', [])],
                    identity_files=[resolve(p) for p in cases.get('identity_files', [])],
                    hosts=list(cases.get('hosts', ['codex', 'claude'])),
                    host_modules=list(hosts.get('modules', [])), models=dict(hosts.get('models', {})),
                    state=resolve(run['state_dir']) if 'state_dir' in run else None,
                    defaults=defaults, limits=limits)
    return config


def render(name, skills, layout, namespace, cases, hosts, models=None):
    """The text of a new seatrial.toml; `init` writes it."""
    lines = ['# seatrial configuration. Paths are relative to this file.', '',
             '[project]', f'name = {json.dumps(name)}', '',
             '[skills]', '# The folder under evaluation. Compare mounts it from Git revisions.',
             f'path = {json.dumps(skills)}',
             '# "plugin": a Claude Code plugin root with skills/. "skills": a folder of <name>/SKILL.md.',
             f'layout = {json.dumps(layout)}',
             '# Claude invokes skills as /<namespace>:<skill>.',
             f'namespace = {json.dumps(namespace)}', '',
             '[cases]', f'path = {json.dumps(cases)}', f'hosts = {json.dumps(hosts)}',
             '# Folders added to sys.path for case.py imports, and files hashed into every case identity.',
             'python_path = []', 'identity_files = []', '',
             '[run]', 'n = 3', 'jobs = 1', 'timeout = 180', '']
    if models:
        lines += ['[hosts.models]', *(f'{h} = {json.dumps(m)}' for h, m in models.items()), '']
    return '\n'.join(lines)


_ACTIVE = None


def activate(config):
    """Make config the one library code reads, and add its case import paths."""
    global _ACTIVE
    _ACTIVE = config
    for path in reversed(config.python_path):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return config


def deactivate():
    """Forget the active configuration; tests and embedding programs use this."""
    global _ACTIVE
    _ACTIVE = None


def current():
    """The active configuration; without one, the nearest file from the working directory."""
    if _ACTIVE is None:
        path = find()
        if path is None:
            raise ConfigError(f'no {FILENAME} found here or above; run `seatrial init`')
        activate(load(path))
    return _ACTIVE


def active():
    """The active configuration, or None, without searching."""
    return _ACTIVE
