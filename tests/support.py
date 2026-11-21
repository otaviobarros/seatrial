"""A disposable project for package tests: a Git repository with skills, cases, and seatrial.toml."""
from pathlib import Path
import tempfile

from seatrial import config
from seatrial.checks import git, init_repo

SKILL = '''---
name: {name}
description: Demo skill {name}.
---

Answer in one sentence.
'''


def make_project(root, layout='plugin', skills=('demo', 'other'), commit=True, namespace='demo-plugin'):
    """Write a project under root and activate its configuration. Return the Config."""
    root = Path(root)
    init_repo(root)
    if layout == 'plugin':
        base = root / 'plugin'
        (base / '.claude-plugin').mkdir(parents=True)
        (base / '.claude-plugin/plugin.json').write_text(f'{{"name": "{namespace}"}}\n')
        (base / 'references').mkdir()
        (base / 'references/contract.md').write_text('# Contract\n')
        skill_root, path = base / 'skills', 'plugin'
    else:
        skill_root = path = root / 'skills'
        path = 'skills'
    for name in skills:
        (skill_root / name).mkdir(parents=True)
        (skill_root / name / 'SKILL.md').write_text(SKILL.format(name=name))
    (root / 'evals/cases').mkdir(parents=True)
    (root / config.FILENAME).write_text(
        config.render('demo', path, layout, namespace if layout == 'plugin' else 'skills', 'evals/cases',
                      ['claude', 'codex']).replace('[run]', f'[run]\nstate_dir = "{root / ".state"}"'))
    if commit:
        git(root, 'add', '.')
        git(root, 'commit', '--quiet', '-m', 'feat: seed project')
    return config.activate(config.load(root / config.FILENAME))


class ProjectCase:
    """Mixin for unittest.TestCase: self.project is a fresh, active project."""
    layout = 'plugin'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(config.deactivate)
        self.root = Path(self._tmp.name).resolve() / 'project'
        self.cfg = make_project(self.root, self.layout)
