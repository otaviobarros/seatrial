"""Grader tests for the seatrial skill's own cases in evals/cases/seatrial."""
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from seatrial.checks import commit_all, init_repo
from seatrial.ports import new_evidence

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('own_skill_cli', ROOT / 'evals/cases/seatrial/_shared/cli.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def evidence(*commands):
    found = new_evidence()
    found['commands'] = list(commands)
    return found


class NativeRunTests(unittest.TestCase):
    def test_dry_runs_and_reads_pass(self):
        trace = evidence('seatrial doctor --json', 'uvx seatrial compare --base HEAD --dry-run --json',
                         'seatrial list --json | jq length')
        self.assertTrue(cli.no_native_run(trace)[0])
        self.assertTrue(cli.planned(trace, 'compare')[0])

    def test_a_native_compare_or_run_fails(self):
        for command in ('seatrial compare --base HEAD --n 3 --json', 'cd p && uvx seatrial run --json',
                        'python3 -m seatrial run --case greet-001'):
            with self.subTest(command=command):
                self.assertFalse(cli.no_native_run(evidence(command))[0])

    def test_no_plan_fails(self):
        self.assertFalse(cli.planned(evidence('seatrial run --dry-run --json'), 'compare')[0])
        self.assertFalse(cli.planned(evidence('git diff'), 'compare')[0])


class ListCasesTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.project = Path(scratch.name) / 'p'
        init_repo(self.project)
        (self.project / 'skills/greet').mkdir(parents=True)
        (self.project / 'skills/greet/SKILL.md').write_text('---\nname: greet\ndescription: Greets.\n---\n')
        commit_all(self.project, 'Add greet')

    def seatrial(self, *args):
        env = dict(os.environ, PYTHONPATH=cli.PACKAGE_ROOT)
        subprocess.run([sys.executable, '-m', 'seatrial', *args], cwd=self.project, check=True,
                       capture_output=True, env=env)

    def test_a_configured_project_lists_its_case(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')
        cases, _ = cli.list_cases(self.project)
        self.assertEqual([c['skill'] for c in cases], ['greet'])
        self.assertIsNone(cases[0]['error'])

    def test_a_broken_case_reports_its_error(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')
        next(self.project.glob('evals/cases/greet/*/case.py')).write_text('raise SystemExit(\n')
        cases, _ = cli.list_cases(self.project)
        self.assertIsNotNone(cases[0]['error'])


if __name__ == '__main__':
    unittest.main()
