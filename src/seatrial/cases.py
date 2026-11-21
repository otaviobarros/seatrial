"""Folder-based case discovery.

A case lives in `<cases>/<skill>/<case-id>/`, where `<cases>` is `[cases] path`
in seatrial.toml:

  case.toml   metadata: id, version, skill, suite, rules, hosts, effort, primary, assertions
  case.py     build(project) -> prompt and grade(project, evidence) -> {assertion: (passed, note)}
  fixture/    optional files that build() copies into the project

Before case.py runs, the loader sets `CASE` (this module's Case object), `ID`, and
`FIXTURE` (the fixture directory) in its namespace. A case never imports another case;
shared code goes in `<cases>/<skill>/_shared/` and is listed under `shared` in
case.toml, so a change to it changes the identity of every case that uses it.
"""
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tomllib

from seatrial import config
from seatrial.workspace import manifest

PACKAGE = Path(__file__).resolve().parent
SUITES = ('capability', 'regression')
KINDS = ('rate', 'zero')
ID = re.compile(r'^[a-z][a-z0-9-]*-\d{3}$')
REQUIRED = ('id', 'version', 'skill', 'suite', 'summary', 'primary', 'assertions')


def case_root():
    """The case folder of the active configuration."""
    return config.current().cases


def _identity_files():
    active = config.active()
    return [PACKAGE / 'checks.py', *(active.identity_files if active else [])]


@dataclass
class Case:
    id: str
    version: int
    skill: str
    suite: str
    summary: str
    primary: str
    assertions: dict
    path: Path
    rules: list = field(default_factory=list)
    hosts: list = field(default_factory=lambda: ['codex', 'claude'])
    effort: str = 'low'
    project: str = 'project'
    timeout: int | None = None
    shared: list = field(default_factory=list)
    module: object = None

    @property
    def fixture(self):
        return self.path / 'fixture'

    def load(self):
        if self.module is None:
            name = 'case_' + self.id.replace('-', '_')
            spec = importlib.util.spec_from_file_location(name, self.path / 'case.py')
            module = importlib.util.module_from_spec(spec)
            module.CASE, module.ID, module.FIXTURE = self, self.id, self.fixture
            module.SHARED = {p.stem: _load_shared(p) for p in self.shared_paths()}
            spec.loader.exec_module(module)
            for required in ('build', 'grade'):
                if not callable(getattr(module, required, None)):
                    raise ValueError(f'{self.id}: case.py must define {required}()')
            self.module = module
        return self.module

    def build(self, project):
        prompt = self.load().build(project)
        if re.match(r'\s*[$/]', prompt):
            raise ValueError(f'{self.id}: build() must not include the skill invocation; '
                             'the host adapter adds it')
        return prompt.lstrip()

    def grade(self, project, evidence):
        return self.load().grade(project, evidence)

    def shared_paths(self):
        return [(self.path / s).resolve() for s in self.shared]

    def identity(self):
        """Hash the grader code and fixture inputs that a case version stands for."""
        files = [self.path / 'case.toml', self.path / 'case.py',
                 *(p for p in self.shared_paths() if p.is_file()), *_identity_files()]
        grader = hashlib.sha256(b''.join(f.read_bytes() for f in files)).hexdigest()
        fixtures = {}
        for directory in [self.fixture, *(p for p in self.shared_paths() if p.is_dir())]:
            if directory.is_dir():
                fixtures[os.path.relpath(directory, self.path.parents[1])] = manifest(directory)
        fixture = hashlib.sha256(json.dumps(fixtures, sort_keys=True).encode()).hexdigest() if fixtures else None
        return {'version': self.version, 'grader_sha256': grader, 'fixture_sha256': fixture}


_SHARED = {}


def _load_shared(path):
    """Load a shared module once per path; directories are fixture data, not code."""
    if path.is_dir():
        return path
    if path not in _SHARED:
        name = 'shared_' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _SHARED[path] = module
    return _SHARED[path]


def parse(path, root=None):
    """Read and validate one case directory."""
    root = Path(root or case_root())
    meta = tomllib.loads((path / 'case.toml').read_text())
    problems = [f'missing {key}' for key in REQUIRED if key not in meta]
    if problems:
        raise ValueError(f'{path}: ' + ', '.join(problems))
    unknown = set(meta) - set(Case.__dataclass_fields__) - {'path', 'module'}
    if unknown:
        raise ValueError(f'{path}: unknown keys {sorted(unknown)}')
    if 'hosts' not in meta and config.active():
        meta['hosts'] = list(config.active().hosts)
    case = Case(path=path, **meta)
    if not ID.match(case.id) or path.name != case.id:
        problems.append(f'id {case.id!r} must match the folder name and look like name-001')
    if path.parent.name != case.skill:
        problems.append(f'skill {case.skill!r} must match the parent folder {path.parent.name!r}')
    if case.suite not in SUITES:
        problems.append(f'suite must be one of {SUITES}')
    if case.primary not in case.assertions:
        problems.append('primary must be an assertion')
    if any(kind not in KINDS for kind in case.assertions.values()):
        problems.append(f'assertion kinds must be one of {KINDS}')
    if not (path / 'case.py').is_file():
        problems.append('case.py is missing')
    for shared in case.shared_paths():
        if not shared.exists() or root.resolve() not in shared.parents:
            problems.append(f'shared path {shared} must exist inside the case folder {root}')
    if problems:
        raise ValueError(f'{path}: ' + '; '.join(problems))
    return case


def discover(root=None):
    root = Path(root or case_root())
    cases = {}
    for toml in sorted(root.glob('*/*/case.toml')):
        if toml.parent.name.startswith('_'):
            continue
        case = parse(toml.parent, root)
        if case.id in cases:
            raise ValueError(f'duplicate case id {case.id}')
        cases[case.id] = case
    return cases


def select(cases, ids=(), skills=(), rules=(), suites=(), hosts=()):
    """Cases matching every given filter; each filter matches any of its values."""
    chosen = []
    for case in cases.values():
        if ids and case.id not in ids:
            continue
        if skills and case.skill not in skills:
            continue
        if rules and not set(rules) & set(case.rules):
            continue
        if suites and case.suite not in suites:
            continue
        if hosts and not set(hosts) & set(case.hosts):
            continue
        chosen.append(case)
    missing = set(ids) - set(cases)
    if missing:
        raise ValueError(f'unknown case ids: {", ".join(sorted(missing))}')
    return chosen
