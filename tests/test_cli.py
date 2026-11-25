"""Setup, configuration, and the agent-facing CLI contract; no native calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from seatrial import cli, config, interactive, setup
from tests.support import ProjectCase, make_project
from tests.test_cases import GOOD_TOML, write_case


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(['--no-color', *argv])
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


class ConfigTests(unittest.TestCase):
    def tearDown(self):
        config.deactivate()

    def test_unknown_keys_bad_layout_and_bad_limits_are_rejected(self):
        for data, reason in (({'skills': {'path': '.'}, 'extra': {}}, 'unknown keys'),
                             ({'skills': {'path': '.', 'colour': 1}}, 'skills.colour'),
                             ({'skills': {'path': '.', 'layout': 'flat'}}, 'layout'),
                             ({'skills': {'path': '.'}, 'limits': {'max_n': 0}}, 'max_n'),
                             ({'skills': {'path': '.'}, 'run': {'n': '3'}}, 'run.n must be a positive integer'),
                             ({'skills': {'path': '.'}, 'run': {'jobs': True}}, 'run.jobs'),
                             ({'skills': {'path': '.'}, 'cases': {'hosts': 'claude'}}, 'cases.hosts must be a list'),
                             ({'skills': {'path': '.'}, 'hosts': {'models': {'claude': 1}}}, 'hosts.models'),
                             ({'skills': {'path': 3}}, 'skills.path must be a string'),
                             ({}, 'path is required')):
            with self.subTest(reason=reason), self.assertRaisesRegex(config.ConfigError, reason):
                config.from_dict(data, '.')

    def test_find_walks_up_and_paths_resolve_against_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'p'
            cfg = make_project(root)
            nested = root / 'a/b'
            nested.mkdir(parents=True)
            self.assertEqual(config.find(nested), root / config.FILENAME)
            self.assertEqual(cfg.skills, root / 'plugin')
            self.assertEqual(cfg.namespace, 'demo-plugin')


class InitTests(unittest.TestCase):
    def tearDown(self):
        config.deactivate()

    def test_init_detects_a_skills_folder_and_creates_a_runnable_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'greeter'
            (root / 'skills/greet').mkdir(parents=True)
            (root / 'skills/greet/SKILL.md').write_text('---\nname: greet\ndescription: Greets.\n---\n')
            code, out, _ = call('--config', str(root / config.FILENAME), 'init', '--yes', '--json')
            self.assertEqual(code, 0)
            result = json.loads(out)
            self.assertEqual((result['layout'], result['namespace'], result['skills_found']),
                             ('skills', 'greeter', ['greet']))
            code, out, _ = call('--config', str(root / config.FILENAME), 'list', '--json')
            self.assertEqual([c['id'] for c in json.loads(out)], ['greet-smoke-001'])
            code, _, err = call('--config', str(root / config.FILENAME), 'init', '--yes')
            self.assertEqual(code, cli.EXIT['config'])
            self.assertIn('--force', err)
            code, _, err = call('--config', str(root / config.FILENAME), 'init', '--yes', '--force', '--host', 'claude')
            self.assertEqual(code, 0, err)
            self.assertIn('hosts = ["claude"]', (root / config.FILENAME).read_text())
            code, out, err = call('--config', str(root / config.FILENAME), 'run', '--dry-run', '--json')
            self.assertEqual(code, 0, err)
            self.assertGreater(json.loads(out)['count'], 0)
            code, _, err = call('--config', str(root / config.FILENAME), 'compare', '--base', 'main', '--dry-run')
            self.assertEqual(code, cli.EXIT['usage'])
            self.assertIn('needs the skills folder in a Git checkout', err)

    def test_find_skills_looks_inside_dot_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'p/linked').mkdir(parents=True)
            (Path(tmp) / 'p/linked/.git').write_text('gitdir: /repo/.git/worktrees/linked\n')
            (Path(tmp) / 'p/vendor').mkdir()
            (Path(tmp) / 'p/vendor/.git').write_text('gitdir: ../.git/modules/vendor\n')
            for folder in ('.claude/skills/greet', '.worktrees/copy/.claude/skills/greet', '.scratch/skills/greet',
                           '.claude/worktrees/wt/skills/greet', 'linked/skills/greet', 'vendor/skills/greet',
                           'vendor/skills/wave'):
                skill = Path(tmp) / 'p' / folder
                skill.mkdir(parents=True)
                (skill / 'SKILL.md').write_text('---\nname: greet\ndescription: Greets.\n---\n')
            found = setup.find_skills(Path(tmp) / 'p')
            base = (Path(tmp) / 'p').resolve()
            self.assertEqual([f['path'] for f in found], [base / 'vendor/skills', base / '.claude/skills'])

    def test_find_skills_prefers_a_plugin_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp) / 'p')
            found = setup.find_skills(Path(tmp) / 'p')
            self.assertEqual(found[0]['layout'], 'plugin')
            self.assertEqual(found[0]['skills'], ['demo', 'other'])


class CommandTests(ProjectCase, unittest.TestCase):
    def run_cli(self, *argv):
        return call('--config', str(self.cfg.file), *argv)

    def test_doctor_json_reports_every_check(self):
        code, out, _ = self.run_cli('doctor', '--no-probe', '--json')
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(report['ok'])
        checks = {c['check'] for c in report['checks']}
        self.assertTrue({'python', 'git', 'config', 'skills', 'cases', 'revisions', 'state'} <= checks)

    def test_doctor_checks_an_explicit_config_and_never_the_one_found_here(self):
        previous = os.getcwd()
        os.chdir(self.root)  # A valid seatrial.toml is found from here.
        self.addCleanup(os.chdir, previous)
        broken = self.root / 'broken.toml'
        broken.write_text('[skills\n')
        for path in (self.root / 'missing.toml', broken):
            with self.subTest(path=path.name):
                config.deactivate()
                code, out, _ = call('--config', str(path), 'doctor', '--no-probe', '--json')
                report = json.loads(out)
                self.assertEqual(code, cli.EXIT['config'])
                self.assertFalse(report['ok'])
                check = next(c for c in report['checks'] if c['check'] == 'config')
                self.assertEqual(check['level'], 'fail')
                self.assertIn(str(path), check['detail'])

    def test_malformed_case_metadata_is_a_json_usage_error(self):
        for field, toml in (('id', GOOD_TOML.replace('id = "demo-001"', 'id = 1')),
                            ('assertions', GOOD_TOML.replace('assertions = { behavior = "rate", preserved = "zero" }',
                                                             'assertions = ["behavior"]'))):
            directory = self.cfg.cases / 'demo' / 'demo-001'
            shutil.rmtree(directory, ignore_errors=True)
            write_case(self.cfg.cases, toml)
            for argv in (['list', '--json'], ['run', '--dry-run', '--json']):
                with self.subTest(field=field, argv=argv):
                    code, out, err = self.run_cli(*argv)
                    error = json.loads(out)
                    self.assertEqual((code, error['ok'], error['exit']), (cli.EXIT['usage'], False, 2))
                    self.assertIn('demo-001/case.toml', error['error'])
                    self.assertIn(f'{field} must be', error['error'])
                    self.assertNotIn('Traceback', err)

    def test_held_out_cases_are_listed_but_run_only_with_holdout(self):
        write_case(self.cfg.cases)
        write_case(self.cfg.cases, GOOD_TOML.replace('demo-001', 'demo-002') + 'holdout = true\n', name='demo-002')
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual([(c['id'], c['holdout']) for c in json.loads(out)],
                         [('demo-001', False), ('demo-002', True)])
        planned = {}
        for flag in ([], ['--holdout']):
            code, out, err = self.run_cli('run', '--dry-run', '--json', *flag)
            self.assertEqual(code, 0, err)
            planned[bool(flag)] = {t['case'] for t in json.loads(out)['trials']}
        self.assertEqual(planned, {False: {'demo-001'}, True: {'demo-002'}})
        code, out, _ = self.run_cli('run', '--case', 'demo-002', '--dry-run', '--json')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertIn('--holdout', json.loads(out)['error'])

    def test_a_case_that_cannot_load_stops_the_plan_and_is_flagged_in_list(self):
        write_case(self.cfg.cases)
        write_case(self.cfg.cases, GOOD_TOML.replace('demo-001', 'demo-002'),
                   'from missing_helpers import probe\n', name='demo-002')
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual(code, 0)
        errors = {c['id']: c['error'] for c in json.loads(out)}
        self.assertIsNone(errors['demo-001'])
        self.assertIn("demo-002: case.py cannot load: ModuleNotFoundError", errors['demo-002'])
        for command in (['run'], ['compare', '--base', 'HEAD']):
            with self.subTest(command=command):
                code, out, _ = self.run_cli(*command, '--dry-run', '--json')
                self.assertEqual(code, cli.EXIT['usage'])
                self.assertIn('demo-002: case.py cannot load', json.loads(out)['error'])
        self.assertFalse(self.cfg.state.exists())
        code, out, err = self.run_cli('run', '--case', 'demo-001', '--dry-run', '--json')
        self.assertEqual(code, 0, err)

    def test_interactive_menu_keeps_held_out_cases_apart(self):
        write_case(self.cfg.cases)
        write_case(self.cfg.cases, GOOD_TOML.replace('demo-001', 'demo-002') + 'holdout = true\n', name='demo-002')
        offered = []

        def choose(question, options, default=0):
            offered.append([value for value, _ in options])
            return answers.pop(0)
        with mock.patch.object(interactive.ui, 'choose', choose):
            answers = ['case', 'demo-001']
            self.assertEqual(interactive.pick_cases(), ['--case', 'demo-001'])
            answers = ['holdout']
            self.assertEqual(interactive.pick_cases(), ['--holdout'])
        self.assertEqual(offered[0], ['all', 'skill', 'case', 'holdout'])
        self.assertEqual(offered[1], ['demo-001'])

    def test_schema_lists_the_exit_codes_and_evidence_keys(self):
        code, out, _ = self.run_cli('schema', '--json')
        schema = json.loads(out)
        self.assertEqual(schema['exit_codes'], cli.EXIT)
        self.assertIn('final_message', schema['evidence_keys'])
        self.assertEqual(schema['evidence_keys']['tools'], 'list (optional)')
        self.assertEqual(schema['evidence_keys']['mcp_calls'], 'list (optional)')
        self.assertIn('mcp', schema['case_toml']['optional'])
        self.assertEqual(set(schema['case_toml']['mcp']), {'name', 'responses'})
        self.assertIn('responses', schema['mcp_responses']['<tool name>'])
        self.assertEqual(schema['report_formats'], ['table', 'markdown', 'comment'])
        self.assertIn('not_significant', schema['evidence_levels'])
        self.assertIn('trials_needed', schema['interpretation_fields'])

    def test_empty_project_lists_no_cases_and_runs_report_nothing(self):
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual((code, json.loads(out)), (0, []))
        code, out, _ = self.run_cli('runs', '--json')
        self.assertEqual((code, json.loads(out)), (0, []))
        code, _, err = self.run_cli('report')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertIn('no retained runs', err)

    def test_new_case_writes_quoted_text_as_valid_toml(self):
        summary = 'Says "hi" first.\\ Then stops.'
        code, _, err = self.run_cli('new-case', '--skill', 'demo', '--id', 'demo-002', '--summary', summary, '--json')
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual(json.loads(out)[0]['summary'], summary)
        for number, text in (('004', 'Waves \U0001f44b first.'), ('005', 'Tab\tand delete\x7f.')):
            code, _, err = self.run_cli('new-case', '--skill', 'demo', '--id', f'demo-{number}', '--summary', text)
            self.assertEqual(code, 0, err)
        code, out, err = self.run_cli('list', '--json')
        self.assertEqual(code, 0, err)
        self.assertIn('Waves \U0001f44b first.', [case['summary'] for case in json.loads(out)])
        code, _, err = self.run_cli('new-case', '--skill', '../outside', '--id', 'demo-003', '--json')
        self.assertEqual(code, cli.EXIT['usage'])

    def test_unreadable_case_names_its_file(self):
        broken = self.cfg.cases / 'demo/demo-009'
        broken.mkdir(parents=True)
        (broken / 'case.toml').write_text('id = "demo-009\n')
        code, _, err = self.run_cli('list')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertIn('demo-009/case.toml', err)

    def test_errors_are_json_under_json(self):
        code, out, err = self.run_cli('run', '--n', '0', '--dry-run', '--json')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertEqual(json.loads(out), {'ok': False, 'exit': 2, 'error': json.loads(out)['error']})
        self.assertIn('--n', json.loads(out)['error'])
        self.assertEqual(err, '')
        code, out, _ = self.run_cli('list', '--no-such-flag', '--json')
        self.assertEqual((code, json.loads(out)['exit']), (2, 2))
        self.cfg.file.write_text(self.cfg.file.read_text().replace('n = 3', 'n = "3"'))
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual(code, cli.EXIT['config'])
        self.assertIn('run.n must be a positive integer', json.loads(out)['error'])

    def test_missing_configuration_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            self.addCleanup(os.chdir, previous)
            code, _, err = call('list')
        self.assertEqual(code, cli.EXIT['config'])
        self.assertIn('seatrial init', err)


if __name__ == '__main__':
    unittest.main()
