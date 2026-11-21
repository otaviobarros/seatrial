"""Runner and CLI end to end with a fake host; no native model calls."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from seatrial import cases, cli, hosts, runner
from seatrial.ports import HostAdapter, new_evidence
from tests.support import ProjectCase
from tests.test_cases import GOOD_TOML, write_case

# The fake agent writes one file and a trace. 'fail' in the prompt skips the file.
AGENT = '''import json, pathlib, sys
prompt = sys.argv[-1]
if 'fail' not in prompt:
    pathlib.Path('hello.txt').write_text('hello\\n')
json.dump({'final': 'done', 'commands': ['echo hello']}, open(sys.argv[1], 'w'))
'''
CASE_PY = '''from seatrial.checks import commit_all, init_repo, preserved


def build(project):
    init_repo(project)
    (project / 'README.md').write_text('demo\\n')
    commit_all(project, 'Initial project')
    return 'Write hello.txt.' if CASE.id == 'demo-001' else 'Write hello.txt and report.'


def grade(project, evidence):
    return {'behavior': ((project / 'hello.txt').is_file(), 'hello.txt'),
            'preserved': preserved(evidence, allow=['hello.txt'])}
'''


class Fake(HostAdapter):
    name = 'fake'
    default_model = 'fake-model'

    def version(self):
        return 'fake 1.0'

    def invocation(self, skill):
        return f'/{skill}'

    def prepare(self, trial):
        return trial.plugin

    def command(self, trial):
        return [sys.executable, '-c', AGENT, str(trial.trace), trial.prompt]

    def collect(self, trial):
        evidence = new_evidence()
        if trial.trace.exists():
            data = json.loads(trial.trace.read_text())
            evidence.update(final_message=data['final'], commands=data['commands'], terminal=True)
        return evidence


class RunnerTests(ProjectCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        root = self.cfg.cases
        write_case(root, py=CASE_PY)
        write_case(root, GOOD_TOML.replace('demo-001', 'demo-002'), CASE_PY, name='demo-002')
        for directory in root.glob('demo/*'):
            toml = directory / 'case.toml'
            toml.write_text(toml.read_text() + 'hosts = ["fake"]\n')
        fake = Fake()
        patch = mock.patch.object(hosts, 'get', lambda name: fake if name == 'fake' else hosts.available()[name])
        patch.start()
        self.addCleanup(patch.stop)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--no-color', '--config', str(self.cfg.file), *argv])
        return code == 0, out.getvalue()

    def test_dry_run_prints_the_interleaved_plan(self):
        ok, out = self.cli('compare', '--base', 'HEAD', '--case', 'demo-001', '--n', '2', '--dry-run')
        self.assertTrue(ok)
        arms = [line.split()[2].split('=')[0] for line in out.splitlines() if line.startswith('demo-001')]
        self.assertEqual(arms, ['base', 'head', 'head', 'base'])
        self.assertIn('4 trials; no native calls made', out)
        self.assertFalse(self.cfg.state.exists())

    def test_caps_and_selectors_are_enforced(self):
        for argv in (['--n', '11'], ['--n', '0'], ['--max-runs', '0'], ['--timeout', '601'], ['--jobs', '5'], ['--max-minutes', '91'],
                     ['--n', '3', '--max-runs', '5'], ['--case', 'missing-001'], ['--host', 'missing-host'],
                     ['--model', 'other=model']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.cli('compare', '--base', 'HEAD', '--skill', 'demo', '--dry-run', *argv)
        with self.assertRaises(SystemExit):
            self.cli('compare', '--base', 'HEAD', '--head', 'HEAD', '--dry-run')

    def test_compare_runs_grades_summarizes_and_regrades(self):
        ok, out = self.cli('compare', '--base', 'HEAD', '--skill', 'demo', '--host', 'fake', '--n', '1')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(records), 4)
        for record in records:
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(record['commands'], 1)
            self.assertEqual(record['lines_added'], 1)
            self.assertTrue(record['pass'], record)
            trial = Path(record['trial_dir'])
            self.assertEqual(trial.parent, work / 'trials')
            evidence = json.loads((trial / 'evidence.json').read_text())
            self.assertEqual(evidence['source_changes'], ['hello.txt'])
            self.assertFalse(evidence['git_changed'] or evidence['plugin_changed'])
        self.assertEqual({r['revision']['name'] for r in records}, {'HEAD', 'WORKTREE'})
        summary = json.loads((work / 'summary.json').read_text())
        demo = summary['cases']['demo-001']['hosts']['fake']
        self.assertEqual(demo['arms']['head']['all_pass'], 1)
        self.assertEqual(demo['decision']['verdict'], 'non-inferior')
        self.assertIn('non-inferior', out)
        ok, out = self.cli('regrade', str(work))
        self.assertTrue(ok)
        self.assertTrue((work / 'summary-regraded.json').is_file())
        ok, out = self.cli('report', str(work), '--format', 'markdown')
        self.assertIn('| demo-002 | fake | head |', out)
        ok, out = self.cli('report', 'latest', '--json')
        self.assertEqual(json.loads(out)['run_dir'], str(work))

    def test_unknown_revision_is_a_usage_error_before_any_run_directory(self):
        for extra in (['--dry-run'], []):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as raised:
                self.cli('compare', '--base', 'no-such-revision', '--skill', 'demo', '--host', 'fake', *extra)
            self.assertEqual(raised.exception.code, cli.EXIT['usage'])
        self.assertFalse(self.cfg.state.exists())

    def test_interrupted_run_regrades_from_its_start_record(self):
        ok, out = self.cli('run', '--case', 'demo-001', '--host', 'fake', '--n', '1')
        work = Path(out.split('run directory (local only): ')[1].strip())
        (work / 'summary.json').unlink()  # As after Ctrl-C: records and run.json only.
        with self.assertRaises(SystemExit):
            self.cli('report', str(work))
        ok, out = self.cli('regrade', str(work), '--json')
        self.assertTrue(ok, out)
        self.assertEqual(list(json.loads(out)['summary']['cases']), ['demo-001'])

    def test_runs_and_regrade_stay_within_their_project(self):
        ok, out = self.cli('run', '--case', 'demo-001', '--host', 'fake', '--n', '1')
        work = Path(out.split('run directory (local only): ')[1].strip())
        for name in ('run.json', 'summary.json'):
            data = json.loads((work / name).read_text())
            data['root'] = '/another/project'
            (work / name).write_text(json.dumps(data))
        ok, out = self.cli('runs', '--json')
        self.assertEqual(json.loads(out), [])
        for target in ('latest', str(work)):
            with self.subTest(target=target), self.assertRaises(SystemExit) as raised:
                self.cli('regrade', target)
            self.assertEqual(raised.exception.code, cli.EXIT['usage'])
        self.assertFalse((work / 'summary-regraded.json').exists())

    def test_failing_trial_is_graded_as_a_failure(self):
        cases_root = self.cfg.cases
        (cases_root / 'demo/demo-002/case.py').write_text(CASE_PY.replace("'Write hello.txt and report.'",
                                                                          "'Do not write it: fail.'"))
        ok, out = self.cli('run', '--case', 'demo-002', '--host', 'fake', '--n', '1')
        self.assertTrue(ok)
        work = Path(out.split('run directory (local only): ')[1].strip())
        record = json.loads((work / 'records.jsonl').read_text())
        self.assertFalse(record['pass'])
        self.assertEqual([a['id'] for a in record['assertions'] if not a['passed']], ['behavior'])

    def test_host_errors_stop_the_run_and_are_not_graded(self):
        class Broken(Fake):
            def collect(self, trial):
                evidence = new_evidence()
                evidence['errors'] = ['API Error: 401 unauthorized']
                return evidence
        broken = Broken()
        with mock.patch.object(hosts, 'get', lambda name: broken):
            ok, out = self.cli('run', '--skill', 'demo', '--host', 'fake', '--n', '2')
        self.assertFalse(ok)  # Stopped early.
        work = Path(out.split('run directory (local only): ')[1].strip())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in records], ['auth_error'])
        self.assertNotIn('pass', records[0])
        self.assertIn('invalid trials', out)

    def test_regrade_rejects_changed_retained_files(self):
        ok, out = self.cli('run', '--skill', 'demo', '--host', 'fake', '--n', '1')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        first, second = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        (Path(first['trial_dir']) / 'project/hello.txt').unlink()  # An edit made while inspecting.
        scratch = Path(second['trial_dir']) / 'project/.scratch'
        scratch.mkdir(exist_ok=True)
        (scratch / 'report.json').write_text('{}')  # Graders can read scratch artifacts.
        ok, out = self.cli('regrade', str(work))
        regraded = [json.loads(line) for line in (work / 'records-regraded.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in regraded], ['stale', 'stale'])
        self.assertIn('hello.txt', regraded[0]['errors'][0])
        self.assertIn('.scratch/report.json', regraded[1]['errors'][0])
        self.assertNotIn('pass', regraded[0])

    def test_short_budget_reserves_the_requested_timeout(self):
        ok, out = self.cli('run', '--case', 'demo-001', '--host', 'fake', '--n', '1',
                           '--max-minutes', '1', '--timeout', '30')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        self.assertEqual(json.loads((work / 'records.jsonl').read_text())['status'], 'completed')

    def test_commands_that_name_the_project_are_recorded(self):
        trial = mock.Mock(directory=self.root.parent / 'state/run/trials/ab')
        evidence = {'skills': [], 'commands': ['ls', f'cat {self.root}/evals/cases/demo/demo-001/case.py',
                                               f'cat {self.root}2/notes.txt', f'cd {self.root};ls']}
        found = runner.isolation_findings(Fake(), trial, evidence)
        self.assertEqual(found, {'outside_access': [evidence['commands'][1], evidence['commands'][3]]})
        inside = mock.Mock(directory=self.cfg.state / 'run/trials/ab')
        self.assertEqual(runner.isolation_findings(Fake(), inside, evidence), {})

    def test_schedule_skips_hosts_a_case_does_not_support(self):
        demo = cases.discover()['demo-001']
        self.assertEqual(runner.schedule([demo], ['codex'], ['run'], 3), [])


if __name__ == '__main__':
    unittest.main()
