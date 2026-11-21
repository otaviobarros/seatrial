"""Setup, configuration, and the agent-facing CLI contract; no native calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from seatrial import cli, config, setup
from tests.support import ProjectCase, make_project


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

    def test_schema_lists_the_exit_codes_and_evidence_keys(self):
        code, out, _ = self.run_cli('schema', '--json')
        schema = json.loads(out)
        self.assertEqual(schema['exit_codes'], cli.EXIT)
        self.assertIn('final_message', schema['evidence_keys'])

    def test_empty_project_lists_no_cases_and_runs_report_nothing(self):
        code, out, _ = self.run_cli('list', '--json')
        self.assertEqual((code, json.loads(out)), (0, []))
        code, out, _ = self.run_cli('runs', '--json')
        self.assertEqual((code, json.loads(out)), (0, []))
        code, _, err = self.run_cli('report')
        self.assertEqual(code, cli.EXIT['usage'])
        self.assertIn('no retained runs', err)

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
