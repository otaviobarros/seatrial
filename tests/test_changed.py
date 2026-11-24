"""--changed REF: select the cases that files changed since REF affect."""
import json
import unittest

from seatrial import cli
from seatrial.checks import git, init_repo as git_init
from tests.support import ProjectCase
from tests.test_cases import GOOD_TOML, write_case
from tests.test_cli import call


def case_toml(case_id, skill, shared=()):
    toml = GOOD_TOML.replace('demo-001', case_id).replace('skill = "demo"', f'skill = "{skill}"')
    return toml + (f'shared = {json.dumps(list(shared))}\n' if shared else '')


class ChangedTests(ProjectCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cases = self.cfg.cases
        (cases / 'demo/_shared').mkdir(parents=True)
        (cases / 'demo/_shared/helper.py').write_text('VALUE = 1\n')
        (cases / '_common/data').mkdir(parents=True)
        (cases / '_common/data/input.txt').write_text('data\n')
        (self.root / 'evals/lib.py').write_text('# identity\n')
        write_case(cases, case_toml('demo-001', 'demo', ['../_shared/helper.py']), name='demo-001')
        write_case(cases, case_toml('demo-002', 'demo', ['../../_common/data']), name='demo-002')
        write_case(cases, case_toml('other-001', 'other', ['../../_common/data']), skill='other', name='other-001')
        write_case(cases, case_toml('other-002', 'other'), skill='other', name='other-002')
        self.cfg.file.write_text(self.cfg.file.read_text().replace(
            'identity_files = []', 'identity_files = ["evals/lib.py"]'))
        (self.root / '.gitignore').write_text('.state/\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '--quiet', '-m', 'test: add cases')

    def changed(self, *selectors):
        code, out, err = call('--config', str(self.cfg.file), 'list', '--json', '--changed', 'HEAD', *selectors)
        data = json.loads(out)
        if code:
            return code, data
        return code, {c['id']: c['selected_because'] for c in data}

    def edit(self, relative, text='changed\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.addCleanup(lambda: (git(self.root, 'reset', '--hard', '--quiet'), git(self.root, 'clean', '-fdq')))

    def test_no_change_reports_no_affected_cases(self):
        code, data = self.changed()
        self.assertEqual(code, cli.EXIT['no_cases'])
        self.assertEqual(data, {'ok': False, 'error': 'no affected cases', 'exit': cli.EXIT['no_cases']})

    def test_a_skill_change_selects_that_skills_cases(self):
        self.edit('plugin/skills/demo/SKILL.md')
        self.assertEqual(self.changed(), (0, {'demo-001': ['skill demo'], 'demo-002': ['skill demo']}))

    def test_a_case_change_selects_that_case(self):
        self.edit('evals/cases/other/other-001/case.toml', case_toml('other-001', 'other', ['../../_common/data'])
                  .replace('A demo case.', 'An edited case.'))
        self.assertEqual(self.changed(), (0, {'other-001': ['case files']}))

    def test_an_untracked_file_in_a_case_counts(self):
        self.edit('evals/cases/other/other-002/fixture/new.txt')
        self.assertEqual(self.changed(), (0, {'other-002': ['case files']}))

    def test_a_shared_path_of_one_skill_selects_its_users(self):
        self.edit('evals/cases/demo/_shared/helper.py', 'VALUE = 2\n')
        self.assertEqual(self.changed(), (0, {'demo-001': ['shared evals/cases/demo/_shared/helper.py']}))

    def test_a_shared_folder_used_by_several_skills_selects_every_case(self):
        self.edit('evals/cases/_common/data/input.txt')
        code, found = self.changed()
        self.assertEqual(code, 0)
        self.assertEqual(found, dict.fromkeys(['demo-001', 'demo-002', 'other-001', 'other-002'],
                                              ['shared folder evals/cases/_common']))

    def test_files_every_case_depends_on_select_every_case(self):
        every = {'evals/lib.py': 'identity file evals/lib.py',
                 'seatrial.toml': 'configuration seatrial.toml',
                 'plugin/references/contract.md': 'skills file plugin/references/contract.md'}
        for relative, reason in every.items():
            with self.subTest(relative):
                path = self.root / relative
                path.write_text(path.read_text() + '\n')
                code, found = self.changed()
                git(self.root, 'checkout', '--quiet', '--', relative)
                self.assertEqual(code, 0)
                self.assertEqual(set(found), {'demo-001', 'demo-002', 'other-001', 'other-002'})
                self.assertEqual(found['other-002'], [reason])

    def test_changed_intersects_with_other_selectors(self):
        self.edit('plugin/skills/demo/SKILL.md')
        self.assertEqual(self.changed('--case', 'demo-002'), (0, {'demo-002': ['skill demo']}))
        code, data = self.changed('--skill', 'other')
        self.assertEqual((code, data['error']), (cli.EXIT['no_cases'], 'no affected cases'))
        code, data = self.changed('--skill', 'no-such-skill')
        self.assertEqual(code, cli.EXIT['usage'])

    def test_a_plugin_at_the_repository_root_still_narrows_to_one_case(self):
        self.cfg.file.write_text(self.cfg.file.read_text().replace('path = "plugin"', 'path = "."'))
        (self.root / 'skills/demo').mkdir(parents=True)
        (self.root / 'skills/demo/SKILL.md').write_text('demo\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '--quiet', '-m', 'test: plugin at the root')
        self.edit('evals/cases/demo/demo-002/case.toml', case_toml('demo-002', 'demo', ['../../_common/data'])
                  .replace('A demo case.', 'An edited case.'))
        self.assertEqual(self.changed(), (0, {'demo-002': ['case files']}))
        self.edit('skills/demo/SKILL.md')
        self.assertEqual(self.changed()[1]['demo-001'], ['skill demo'])

    def test_dry_run_shows_why_each_case_runs(self):
        self.edit('plugin/skills/other/SKILL.md')
        code, out, err = call('--config', str(self.cfg.file), 'run', '--dry-run', '--json', '--host', 'claude',
                              '--changed', 'HEAD')
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan['selected_because'], {'other-001': ['skill other'], 'other-002': ['skill other']})
        self.assertEqual({t['case'] for t in plan['trials']}, {'other-001', 'other-002'})
        code, out, _ = call('--config', str(self.cfg.file), 'run', '--dry-run', '--host', 'claude',
                            '--changed', 'HEAD')
        self.assertIn('other-001: skill other', out)

    def test_an_unknown_revision_is_a_usage_error(self):
        for ref in ('no-such-ref', '--output=x'):
            with self.subTest(ref):
                code, out, _ = call('--config', str(self.cfg.file), 'list', '--json', f'--changed={ref}')
                data = json.loads(out)
                self.assertEqual(code, cli.EXIT['usage'])
                self.assertIn('not a Git revision', data['error'])

    def test_files_that_mounts_skip_select_nothing(self):
        self.edit('plugin/skills/.DS_Store')
        self.edit('plugin/skills/demo/.DS_Store')
        code, data = self.changed()
        self.assertEqual(code, cli.EXIT['no_cases'])

    def test_skills_in_another_repository_are_a_usage_error(self):
        other = self.root.parent / 'skills-repo'
        git_init(other)
        self.cfg.file.write_text(self.cfg.file.read_text().replace('path = "plugin"', f'path = "{other}"'))
        code, out, _ = call('--config', str(self.cfg.file), 'list', '--json', '--changed', 'HEAD')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertIn('one Git repository', json.loads(out)['error'])


if __name__ == '__main__':
    unittest.main()
