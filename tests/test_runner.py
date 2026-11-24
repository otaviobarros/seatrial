"""Runner and CLI end to end with a fake host; no native model calls."""
import contextlib
import io
import json
from pathlib import Path
import sys
import concurrent.futures
import tempfile
import threading
import time
import types
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

# A negative case: the request is off-topic, so the skill must stay unloaded.
NEGATIVE_PY = '''from seatrial.checks import skill_triggered


def build(project):
    project.mkdir(parents=True, exist_ok=True)
    return 'List the files.'


def grade(project, evidence):
    triggered, note = skill_triggered(evidence, CASE.skill)
    return {'quiet': (not triggered, note)}
'''


class Fake(HostAdapter):
    name = 'fake'
    default_model = 'fake-model'
    supports_no_plugin = True

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
        self.assertFalse(demo['interpretation']['underpowered'])
        self.assertIsNone(demo['interpretation']['trials_needed'])
        self.assertIn('non-inferior', out)
        ok, out = self.cli('regrade', str(work))
        self.assertTrue(ok)
        self.assertTrue((work / 'summary-regraded.json').is_file())
        ok, out = self.cli('report', str(work), '--format', 'markdown')
        self.assertIn('| demo-002 | fake | head |', out)
        ok, out = self.cli('report', 'latest', '--json')
        self.assertEqual(json.loads(out)['run_dir'], str(work))

    def test_dry_run_names_the_no_skill_arm(self):
        ok, out = self.cli('compare', '--base', 'NONE', '--case', 'demo-001', '--n', '1', '--dry-run', '--json')
        self.assertTrue(ok, out)
        trials = json.loads(out)['trials']
        self.assertEqual({(t['arm'], t['revision'], t['trigger']) for t in trials if t['arm'] == 'base'},
                         {('base', 'NONE', 'auto')})
        self.assertEqual({t['trigger'] for t in trials if t['arm'] == 'head'}, {'explicit'})

    def test_trigger_override_warns_for_cases_it_changes(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(['--no-color', '--config', str(self.cfg.file), 'run', '--case', 'demo-001',
                             '--n', '1', '--trigger', 'auto', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertIn('--trigger auto changes the mode of demo-001', err.getvalue())

    def test_a_harness_error_record_keeps_its_revision_and_trigger(self):
        with mock.patch.object(runner, 'run_trial', side_effect=RuntimeError('boom')):
            ok, out = self.cli('run', '--rev', 'NONE', '--case', 'demo-001', '--host', 'fake', '--n', '1')
        work = Path(out.split('run directory (local only): ')[1].strip())
        record, = map(json.loads, (work / 'records.jsonl').read_text().splitlines())
        self.assertEqual((record['status'], record['trigger'], record['revision']['name']),
                         ('harness_error', 'auto', 'NONE'))

    def test_no_skill_baseline_shows_the_skill_improves_results(self):
        class SkillOnly(Fake):
            def command(self, trial):  # Writes the file only when the prompt invokes the skill.
                prompt = trial.prompt if trial.prompt.startswith('/') else 'fail ' + trial.prompt
                return [sys.executable, '-c', AGENT, str(trial.trace), prompt]

        host, prompts = SkillOnly(), []
        prepare = host.prepare
        host.prepare = lambda trial: prompts.append((trial.plugin, trial.prompt)) or prepare(trial)
        with mock.patch.object(hosts, 'get', lambda name: host):
            ok, out = self.cli('compare', '--base', 'NONE', '--case', 'demo-001', '--host', 'fake', '--n', '10')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        base = [r for r in records if r['arm'] == 'base']
        self.assertEqual(len(base), 10)
        self.assertEqual({r['revision']['name'] for r in base}, {'NONE'})
        self.assertFalse(any(r['pass'] for r in base))
        self.assertTrue(all(r['pass'] for r in records if r['arm'] == 'head'))
        self.assertEqual(sum(plugin is None for plugin, _ in prompts), 10)
        self.assertEqual({r['trigger'] for r in base}, {'auto'})
        self.assertTrue(all(prompt == 'Write hello.txt.' for plugin, prompt in prompts if plugin is None))
        summary = json.loads((work / 'summary.json').read_text())
        demo = summary['cases']['demo-001']['hosts']['fake']
        self.assertEqual(demo['decision']['verdict'], 'improved')
        self.assertEqual(demo['arms']['base']['primary_ci'], [0.0, 0.2775])
        self.assertEqual(demo['arms']['head']['primary_ci'], [0.7225, 1.0])
        self.assertEqual(demo['arms']['head']['assertion_ci'], {'behavior': [0.7225, 1.0], 'preserved': [0.7225, 1.0]})
        self.assertEqual(demo['interpretation']['evidence'], 'strong')
        self.assertIn('0/10 (0–28%) in base and 10/10 (72–100%) in head', demo['interpretation']['explanation'])
        ok, out = self.cli('regrade', str(work))
        self.assertTrue(ok, out)
        ok, out = self.cli('report', str(work), '--format', 'markdown')
        self.assertIn('| demo-001 | fake | none | 10/10 | 0/10 (0–28%) |', out)
        self.assertIn('| demo-001 | fake | head | 10/10 | 10/10 (72–100%) |', out)
        step = work / 'step-summary.md'
        step.write_text('earlier step\n')
        with mock.patch.dict('os.environ', {'GITHUB_STEP_SUMMARY': str(step)}):
            ok, out = self.cli('report', str(work), '--format', 'github')
            self.assertTrue(ok, out)
            self.assertTrue(out.startswith('- **improved**: 1\n'), out)
            self.assertIn('<details>', out)
            self.assertNotIn('Needs attention', out)
            self.assertNotIn(str(work), out)
            self.assertEqual(step.read_text(), 'earlier step\n' + out.rstrip('\n') + '\n\n')
            self.cli('report', str(work), '--format', 'markdown')
        self.assertEqual(step.read_text().count('<details>'), 1)

    def test_compare_format_github_posts_markdown_and_appends_the_step_summary(self):
        step = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'step-summary.md'
        for extra in ([], ['--json']):
            with self.subTest(extra=extra), mock.patch.dict('os.environ', {'GITHUB_STEP_SUMMARY': str(step)}):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(['--no-color', '--config', str(self.cfg.file), 'compare', '--base', 'HEAD',
                                     '--skill', 'demo', '--host', 'fake', '--n', '1', '--format', 'github', *extra])
                self.assertEqual(code, 0, err.getvalue())
                if extra:
                    payload = json.loads(out.getvalue())
                    self.assertIn('underpowered', payload)
                    self.assertTrue(Path(payload['run_dir']).is_dir())
                else:
                    self.assertTrue(out.getvalue().startswith('- **'), out.getvalue())
                    self.assertIn('<details>', out.getvalue())
                    self.assertNotIn('run directory', out.getvalue())
                    self.assertIn('run directory (local only): ', err.getvalue())
        self.assertEqual(step.read_text().count('<summary>Full results</summary>'), 2)

    def test_run_without_the_skill_combines_with_holdout(self):
        toml = self.cfg.cases / 'demo/demo-002/case.toml'
        toml.write_text(toml.read_text() + 'holdout = true\n')
        ok, out = self.cli('run', '--rev', 'NONE', '--holdout', '--host', 'fake', '--n', '1')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual([(r['case_id'], r['revision']['name']) for r in records], [('demo-002', 'NONE')])
        ok, out = self.cli('report', str(work), '--format', 'markdown')
        self.assertIn('| demo-002 | fake | none |', out)

    def test_a_baseline_trial_that_loads_a_skill_is_contaminated_and_not_graded(self):
        class Leaky(Fake):
            def collect(self, trial):
                evidence = super().collect(trial)
                evidence['skills'] = ['demo']  # A user-level copy of the skill under test.
                return evidence

        with mock.patch.object(hosts, 'get', lambda name: Leaky()):
            ok, out = self.cli('run', '--rev', 'NONE', '--case', 'demo-001', '--host', 'fake', '--n', '1')
        work = Path(out.split('run directory (local only): ')[1].strip())
        [record] = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual((record['status'], record['foreign_skills']), ('contaminated', ['demo']))
        self.assertNotIn('pass', record)

    def test_none_is_rejected_for_a_host_without_no_plugin_support(self):
        class PluginOnly(Fake):
            supports_no_plugin = False

        with mock.patch.object(hosts, 'get', lambda name: PluginOnly()), self.assertRaises(SystemExit) as raised:
            self.cli('compare', '--base', 'NONE', '--case', 'demo-001', '--host', 'fake', '--dry-run')
        self.assertEqual(raised.exception.code, cli.EXIT['usage'])

    def test_a_lowercase_none_revision_names_the_reserved_spelling(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(['--no-color', '--config', str(self.cfg.file), 'run', '--rev', 'none', '--dry-run'])
        self.assertIn('use NONE for no skill', err.getvalue())

    def test_auto_trigger_grades_whether_the_skill_loaded_on_its_own(self):
        class Picky(Fake):  # Loads demo for hello requests and another skill otherwise.
            def collect(self, trial):
                evidence = super().collect(trial)
                evidence['skills'] = ['demo-plugin:demo' if 'hello' in trial.prompt else 'demo-plugin:other']
                return evidence

        root = self.cfg.cases / 'demo'
        toml = (root / 'demo-001/case.toml').read_text()
        (root / 'demo-001/case.toml').write_text(toml.replace(
            'primary = "behavior"', 'primary = "skill_triggered"').replace(
            'preserved = "zero" }', 'preserved = "zero", skill_triggered = "rate" }'))
        negative = toml.replace('demo-001', 'demo-003').replace('primary = "behavior"', 'primary = "quiet"').replace(
            '{ behavior = "rate", preserved = "zero" }', '{ quiet = "rate" }')
        write_case(self.cfg.cases, negative, NEGATIVE_PY, name='demo-003')
        host, prompts = Picky(), []
        command = host.command
        host.command = lambda trial: prompts.append(trial.prompt) or command(trial)
        with mock.patch.object(hosts, 'get', lambda name: host):
            ok, out = self.cli('run', '--case', 'demo-001', '--case', 'demo-003', '--host', 'fake',
                               '--n', '1', '--trigger', 'auto', '--dry-run')
            self.assertIn('trigger=auto', out)
            ok, out = self.cli('run', '--case', 'demo-001', '--case', 'demo-003', '--host', 'fake',
                               '--n', '1', '--trigger', 'auto')
        self.assertTrue(ok, out)
        self.assertFalse(any(p.startswith('/') for p in prompts), prompts)
        work = Path(out.split('run directory (local only): ')[1].strip())
        records = {r['case_id']: r for r in map(json.loads, (work / 'records.jsonl').read_text().splitlines())}
        self.assertTrue(records['demo-001']['pass'], records['demo-001'])
        self.assertEqual(records['demo-001']['trigger'], 'auto')
        self.assertNotIn('other_skills', records['demo-001'])
        self.assertTrue(records['demo-003']['pass'], records['demo-003'])
        self.assertEqual(records['demo-003']['other_skills'], ['demo-plugin:other'])
        self.assertEqual(json.loads((work / 'run.json').read_text())['trigger_override'], 'auto')

    def test_other_skills_excludes_the_case_skill_and_every_outside_skill(self):
        class Outside(Fake):
            def foreign_skills(self, trial, evidence):
                return [s for s in evidence['skills'] if s.startswith('/home/')]

        case = types.SimpleNamespace(skill='demo')
        evidence = new_evidence()
        evidence['skills'] = ['SKILL.md', 'demo-plugin:other', *(f'/home/skills/s{i}/SKILL.md' for i in range(12))]
        evidence['skill_reads'] = [{'path': 'SKILL.md', 'cwd': '.agents/skills/demo'}]
        mounted = types.SimpleNamespace(plugin=Path('/plugin'))
        self.assertEqual(runner.other_skills(case, Outside(), mounted, evidence), ['demo-plugin:other'])
        self.assertEqual(runner.other_skills(case, Outside(), types.SimpleNamespace(plugin=None), evidence), [])

    def test_other_skills_classifies_each_read_of_one_relative_path(self):
        class ByDirectory(Fake):
            def foreign_skills(self, trial, evidence):
                return [r['path'] for r in evidence['skill_reads'] if r['cwd'].startswith('/home')]

        evidence = new_evidence()
        evidence['skills'] = ['SKILL.md']
        evidence['skill_reads'] = [{'path': 'SKILL.md', 'cwd': '/home/skills/x'},
                                   {'path': 'SKILL.md', 'cwd': '/plugin/skills/other'}]
        found = runner.other_skills(types.SimpleNamespace(skill='demo'), ByDirectory(),
                                    types.SimpleNamespace(plugin=Path('/plugin')), evidence)
        self.assertEqual(found, ['/plugin/skills/other/SKILL.md'])

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

    def test_interrupt_cancels_queued_trials_and_keeps_finished_records(self):
        calls, lock, both = [], threading.Lock(), threading.Barrier(2, timeout=5)

        def trial(job, context):
            with lock:
                calls.append(job)
                first = len(calls) == 1
            both.wait()  # Both workers hold a trial before the interrupt.
            if first:
                raise KeyboardInterrupt  # Ctrl-C reaches this worker while the other trial runs.
            time.sleep(0.3)
            return {'case_id': job['case'].id, 'case_version': 1, 'host': job['host'], 'arm': job['arm'],
                    'trial_index': job['index'], 'status': 'completed', 'wall_s': 0.3}

        chosen = list(cases.discover().values())
        options = {'n': 2, 'jobs': 2, 'models': {'fake': 'fake-model'}, 'effort': None, 'timeout': None,
                   'budget_usd': None, 'max_minutes': 10, 'max_cost_usd': None}
        with mock.patch.object(runner, 'run_trial', trial), self.assertRaises(KeyboardInterrupt):
            runner.execute(chosen, ['fake'], {'run': 'WORKTREE'}, options)
        self.assertEqual(len(calls), 2)  # Two of four planned trials started; the queued two never did.
        work = next(self.cfg.state.iterdir())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in records], ['completed'])

    def test_interrupt_during_submission_cancels_queued_trials(self):
        calls, running = [], threading.Event()
        Pool = concurrent.futures.ThreadPoolExecutor

        class InterruptedPool(Pool):
            submitted = 0

            def submit(self, *args, **kwargs):
                InterruptedPool.submitted += 1
                if InterruptedPool.submitted == 3:  # Ctrl-C on the main thread: one trial runs, one waits.
                    running.wait(5)
                    raise KeyboardInterrupt
                return super().submit(*args, **kwargs)

        def trial(job, context):
            calls.append(job)
            running.set()
            time.sleep(0.3)
            return {'case_id': job['case'].id, 'case_version': 1, 'host': job['host'], 'arm': job['arm'],
                    'trial_index': job['index'], 'status': 'completed', 'wall_s': 0.3}

        chosen = list(cases.discover().values())
        options = {'n': 2, 'jobs': 1, 'models': {'fake': 'fake-model'}, 'effort': None, 'timeout': None,
                   'budget_usd': None, 'max_minutes': 10, 'max_cost_usd': None}
        with mock.patch.object(runner, 'run_trial', trial), \
                mock.patch.object(concurrent.futures, 'ThreadPoolExecutor', InterruptedPool), \
                self.assertRaises(KeyboardInterrupt):
            runner.execute(chosen, ['fake'], {'run': 'WORKTREE'}, options)
        time.sleep(0.8)  # Long enough for an uncancelled queued trial to start after the interrupt.
        self.assertEqual(len(calls), 1)  # The queued second trial never started.
        work = next(self.cfg.state.iterdir())
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in records], ['completed'])

    def test_regrade_rejects_edited_evidence(self):
        ok, out = self.cli('run', '--case', 'demo-001', '--host', 'fake', '--n', '1')
        self.assertTrue(ok, out)
        work = Path(out.split('run directory (local only): ')[1].strip())
        record = json.loads((work / 'records.jsonl').read_text())
        saved = Path(record['trial_dir']) / 'evidence.json'
        evidence = json.loads(saved.read_text())
        evidence['final_message'] = 'edited while inspecting'
        saved.write_text(json.dumps(evidence) + '\n')
        self.cli('regrade', str(work))
        regraded = json.loads((work / 'records-regraded.jsonl').read_text())
        self.assertEqual(regraded['status'], 'stale')
        self.assertIn('evidence.json', regraded['errors'][0])
        self.assertNotIn('pass', regraded)

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
