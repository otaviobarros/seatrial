"""Grader tests for the seatrial skill's own cases in evals/cases/seatrial."""
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_help_is_not_a_native_run(self):
        trace = evidence('seatrial compare --help 2>&1', 'seatrial run -h', 'seatrial compare --base HEAD --head WORKTREE --help')
        self.assertTrue(cli.no_native_run(trace)[0])

    def test_no_plan_fails(self):
        self.assertFalse(cli.planned(evidence('seatrial run --dry-run --json'), 'compare')[0])
        self.assertFalse(cli.planned(evidence('git diff'), 'compare')[0])

    def test_a_dry_run_continued_on_the_next_line_passes(self):
        trace = evidence('seatrial compare --base HEAD \\\n  --dry-run --json')
        self.assertTrue(cli.no_native_run(trace)[0])
        self.assertTrue(cli.planned(trace, 'compare')[0])

    def test_a_native_run_anywhere_in_the_command_fails(self):
        for command in ('seatrial doctor --json\nseatrial compare --base HEAD --n 3 --json',
                        'seatrial --no-color run --json', 'seatrial --config seatrial.toml compare --base HEAD',
                        'echo "$(seatrial run --json)"', 'out=`seatrial run --json`',
                        'uvx seatrial@latest compare --base HEAD', 'python3.12 -B -m seatrial run --case greet-001'):
            with self.subTest(command=command):
                self.assertFalse(cli.no_native_run(evidence(command))[0])

    def test_a_plan_after_global_options_counts(self):
        self.assertTrue(cli.planned(evidence('seatrial --no-color compare --base HEAD --dry-run'), 'compare')[0])


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

    def test_a_broken_configuration_lists_nothing(self):
        # `seatrial list --json` then prints one error object, not a list of cases.
        (self.project / 'seatrial.toml').write_text('[skills\n')
        cases, note = cli.list_cases(self.project)
        self.assertIsNone(cases)
        self.assertIn('seatrial.toml', note)

    def test_the_candidate_code_gets_no_secrets(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')
        case = next(self.project.glob('evals/cases/greet/*/case.py'))
        case.write_text("import os\nif 'ANTHROPIC_API_KEY' in os.environ:\n    raise SystemExit('saw the key')\n"
                        + case.read_text())
        with mock.patch.dict(os.environ, ANTHROPIC_API_KEY='sk-test'):
            cases, _ = cli.list_cases(self.project)
        self.assertIsNone(cases[0]['error'])

    def test_a_case_that_cannot_fail_does_not_reject_a_silent_agent(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')  # The template checks skill-loaded only.
        self.assertFalse(cli.rejects_silent_agent(self.project)[0])

    def test_a_case_that_checks_the_greeting_rejects_a_silent_agent(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')
        case = next(self.project.glob('evals/cases/greet/*/case.py'))
        case.write_text(GREETING_CASE)
        toml = case.parent / 'case.toml'
        toml.write_text(toml.read_text().replace('primary = "skill-loaded"', 'primary = "named"')
                        .replace('{ skill-loaded = "rate", preserved = "zero" }', '{ named = "rate" }'))
        passed, note = cli.rejects_silent_agent(self.project)
        self.assertTrue(passed, note)

    def test_a_case_that_checks_only_the_skill_call_does_not_reject_a_silent_agent(self):
        self.seatrial('init', '--yes', '--host', 'claude', '--json')
        case = next(self.project.glob('evals/cases/greet/*/case.py'))
        case.write_text(GREETING_CASE.replace("report_matches(evidence, r'hello,? ana')", "tool_used(evidence, 'Skill')")
                        .replace('report_matches', 'tool_used'))
        toml = case.parent / 'case.toml'
        toml.write_text(toml.read_text().replace('primary = "skill-loaded"', 'primary = "named"')
                        .replace('{ skill-loaded = "rate", preserved = "zero" }', '{ named = "rate" }'))
        self.assertFalse(cli.rejects_silent_agent(self.project)[0])


    def test_an_unedited_new_case_does_not_count_beside_a_real_check(self):
        self.test_a_case_that_checks_the_greeting_rejects_a_silent_agent()
        self.seatrial('new-case', '--skill', 'greet', '--id', 'greet-002', '--summary', 'Greets.', '--json')
        passed, note = cli.rejects_silent_agent(self.project)
        self.assertFalse(passed)
        self.assertIn('greet-002: new-case', note)


class ScopedPlanTests(unittest.TestCase):
    SCOPES = (r'--changed(?:=|\s+)(?!-)\S+', r'--skill[=\s]+greet\b', r'--case[=\s]+greet-001\b')

    def scoped(self, *commands):
        return cli.scoped_plan(evidence(*commands), 'compare', self.SCOPES, ('farewell',))[0]

    def test_a_plan_scoped_to_the_edit_passes(self):
        for command in ('seatrial compare --base HEAD --changed HEAD --dry-run --json',
                        'uvx seatrial compare --base HEAD --skill greet --dry-run',
                        'seatrial compare --base HEAD --case=greet-001 --dry-run --json'):
            with self.subTest(command=command):
                self.assertTrue(self.scoped(command))

    def test_an_unscoped_or_wrong_plan_fails(self):
        for command in ('seatrial compare --base HEAD --dry-run --json',
                        'seatrial compare --base HEAD --skill greet --skill farewell --dry-run',
                        'seatrial compare --base HEAD --case greet-0012 --dry-run',
                        'seatrial run --changed HEAD --dry-run --json',
                        'seatrial compare --base HEAD --changed --dry-run --json',
                        'seatrial compare --base HEAD --changed HEAD --n 3 --json'):
            with self.subTest(command=command):
                self.assertFalse(self.scoped(command))

    def test_one_scoped_plan_among_others_passes(self):
        self.assertTrue(self.scoped('seatrial compare --base HEAD --dry-run --json',
                                    'seatrial compare --base HEAD --changed HEAD --dry-run --json'))


class SizedPlanTests(unittest.TestCase):
    def sized(self, *commands):
        return cli.sized_plan(evidence(*commands), 'compare', 10)[0]

    def test_a_plan_with_ten_or_more_trials_passes(self):
        for command in ('seatrial compare --base HEAD --n 10 --dry-run --json',
                        'uvx seatrial compare --base HEAD --dry-run --n=12'):
            with self.subTest(command=command):
                self.assertTrue(self.sized(command))

    def test_a_small_missing_or_unplanned_size_fails(self):
        for command in ('seatrial compare --base HEAD --n 3 --dry-run',
                        'seatrial compare --base HEAD --dry-run --json',
                        'seatrial compare --base HEAD --n $n --dry-run',
                        'seatrial compare --base HEAD --n 10 --json',
                        'seatrial compare --base HEAD --n 10 --n 3 --dry-run',
                        'seatrial run --n 10 --dry-run'):
            with self.subTest(command=command):
                self.assertFalse(self.sized(command))


class OnlyChangedTests(unittest.TestCase):
    def test_allowed_paths_pass_and_others_fail(self):
        found = evidence()
        found.update(source_changes=['seatrial.toml', 'evals/cases/greet/greet-001/case.py'], git_changed=False)
        self.assertTrue(cli.only_changed(found, ('seatrial.toml', 'evals/'))[0])
        found['source_changes'].append('skills/greet/SKILL.md')
        self.assertEqual(cli.only_changed(found, ('seatrial.toml', 'evals/')), (False, 'skills/greet/SKILL.md'))

    def test_a_git_change_fails(self):
        found = evidence()
        found.update(source_changes=[], git_changed=True, git_changes=['.git/refs/heads/main'])
        self.assertEqual(cli.only_changed(found, ('evals/',)), (False, 'Git: .git/refs/heads/main'))


GREETING_CASE = """from seatrial.checks import commit_all, init_repo, report_matches


def build(project):
    init_repo(project)
    (project / 'notes.txt').write_text('Visitor log\\n')
    commit_all(project, 'Initial project')
    return 'Hi, I am Ana. Can you help me read notes.txt?'


def grade(project, evidence):
    return {'named': report_matches(evidence, r'hello,? ana')}
"""


if __name__ == '__main__':
    unittest.main()
