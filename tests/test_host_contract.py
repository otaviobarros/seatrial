"""Contract tests that every registered host adapter must pass.

Each adapter needs recorded traces in `src/seatrial/hosts/samples/<host>/`: `<name>.jsonl`
and `<name>.expected.json`, which lists evidence values and the expected status. Optional
keys: `foreign_skills` and `unknown_skills` (provenance) and `trial_project` (the project path).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from seatrial import hosts
from seatrial.hosts import codex
from seatrial import checks
from tests.support import ProjectCase
from seatrial.ports import GIT_IDENTITY, HostAdapter, Trial, validate_evidence

SAMPLES = Path(hosts.__file__).parent / 'samples'


def trial(directory, prompt='Check the change.', project='/project'):
    return Trial(directory, Path(project), Path('/plugin'), prompt, 'model-x', 'low', 60)


class HostContractTests(ProjectCase, unittest.TestCase):
    def test_registry_finds_every_adapter_module(self):
        found = hosts.available()
        self.assertGreaterEqual(set(found), {'claude', 'codex'})
        for name, adapter in found.items():
            self.assertIsInstance(adapter, HostAdapter)
            self.assertTrue(adapter.default_model, name)
        with self.assertRaises(ValueError):
            hosts.get('missing-host')

    def test_every_adapter_has_recorded_samples(self):
        for name in hosts.available():
            self.assertTrue(list((SAMPLES / name).glob('*.jsonl')), f'{name} has no sample traces')

    def test_samples_parse_to_valid_expected_evidence(self):
        for name, adapter in hosts.available().items():
            for sample in sorted((SAMPLES / name).glob('*.jsonl')):
                expected = json.loads((sample.parent / (sample.stem + '.expected.json')).read_text())
                status = expected.pop('status')
                foreign = expected.pop('foreign_skills', None)
                unknown = expected.pop('unknown_skills', [])
                project = expected.pop('trial_project', '/project')
                with self.subTest(host=name, sample=sample.name), tempfile.TemporaryDirectory() as tmp:
                    t = trial(Path(tmp), project=project)
                    shutil.copy(sample, t.trace)
                    evidence = adapter.collect(t)
                    self.assertEqual(validate_evidence(evidence), [])
                    for key, value in expected.items():
                        self.assertEqual(evidence[key], value, key)
                    if foreign is not None:
                        self.assertEqual(adapter.foreign_skills(t, evidence), foreign)
                    self.assertEqual(adapter.unknown_skills(t, evidence), unknown)
                    process = {'exit': 0 if status in ('completed', 'trace_incomplete') else 1,
                               'started': 0, 'ended': 1}
                    self.assertEqual(adapter.classify(process, evidence, ''), status)

    def test_missing_trace_is_valid_and_not_terminal(self):
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                evidence = adapter.collect(trial(Path(tmp)))
                self.assertEqual(validate_evidence(evidence), [])
                self.assertFalse(evidence['terminal'])
                self.assertEqual(adapter.classify({'exit': 0, 'started': 0, 'ended': 1}, evidence, ''),
                                 'incomplete')

    def test_every_delegate_must_be_traced(self):
        done = {'exit': 0, 'started': 0, 'ended': 1}
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                evidence = adapter.collect(trial(Path(tmp)))
                evidence.update(terminal=True, delegations=2)
                for traced, status in ((0, 'trace_incomplete'), (1, 'trace_incomplete'), (2, 'completed')):
                    evidence['delegation_traces'] = traced
                    self.assertEqual(adapter.classify(done, evidence, ''), status, traced)

    def test_command_ends_with_prompt_and_pin_ignores_paths(self):
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                t = trial(Path(tmp), adapter.invocation('verify-change') + '\nCheck it.')
                self.assertIn('verify-change', t.prompt)
                command = adapter.command(t)
                self.assertEqual(command[-1], t.prompt)
                self.assertIn('model-x', command)
                self.assertEqual(adapter.pin('model-x', 'low'), adapter.pin('model-x', 'low'))
                self.assertNotEqual(adapter.pin('model-x', 'low'), adapter.pin('model-y', 'low'))

    def test_environment_keeps_temporary_files_in_project_scratch(self):
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                t = trial(Path(tmp))
                t.state['home'] = Path(tmp) / 'home'
                env = adapter.environment(t, {'PATH': os.environ.get('PATH', ''), 'CLAUDECODE': '1'})
                for key in ('TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME'):
                    self.assertEqual(env[key], '/project/.scratch')
                if name == 'claude':
                    self.assertNotIn('CLAUDECODE', env)
                    self.assertEqual(env['CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'], '1')

    def test_environment_passes_only_allowed_variables(self):
        base = {'PATH': '/bin', 'HOME': '/home/u', 'PWD': str(self.root), 'OLDPWD': '/x', 'LC_ALL': 'C',
                'SECRET_TOKEN': 's', 'MY_PROXY_TOKEN': 'p', 'OPENAI_API_KEY': 'o', 'ANTHROPIC_API_KEY': 'a'}
        self.cfg.pass_env = ['MY_*']
        expected = {'claude': 'ANTHROPIC_API_KEY', 'codex': 'OPENAI_API_KEY'}
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                t = trial(Path(tmp))
                t.state['home'] = Path(tmp) / 'home'
                env = adapter.environment(t, base)
                for key in ('PWD', 'OLDPWD', 'SECRET_TOKEN'):
                    self.assertNotIn(key, env)
                for key in ('PATH', 'LC_ALL', 'MY_PROXY_TOKEN', expected[name]):
                    self.assertIn(key, env)
                for other in set(expected.values()) - {expected[name]}:
                    self.assertNotIn(other, env)
                if name == 'claude':
                    self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(Path(tmp) / 'claude-config'))
                    adapter.release(t, threading.Lock())
                    self.assertFalse((Path(tmp) / 'claude-config').exists())

    def test_claude_reports_skills_from_outside_the_plugin(self):
        adapter = hosts.get('claude')
        evidence = {'skills': ['demo-plugin:demo', 'personal-notes', '/plugin/skills/demo/SKILL.md',
                               '/home/u/.claude/skills/x/SKILL.md']}
        self.assertEqual(adapter.foreign_skills(trial(Path('/t')), evidence),
                         ['personal-notes', '/home/u/.claude/skills/x/SKILL.md'])

    def test_environment_isolates_git_from_user_and_system_config(self):
        for name, adapter in hosts.available().items():
            with self.subTest(host=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                hostile = hostile_git_config(root)
                t = trial(root)
                t.state['home'] = root / 'home'
                env = adapter.environment(t, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root),
                                              'GIT_CONFIG_GLOBAL': str(hostile), 'GIT_DIR': '/elsewhere',
                                              'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'commit.gpgsign',
                                              'GIT_CONFIG_VALUE_0': 'true'})
                self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
                self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
                for key in ('GIT_DIR', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0'):
                    self.assertNotIn(key, env)
                project = root / 'project'
                project.mkdir()
                run = lambda *args: subprocess.run(['git', '-C', str(project), *args], env=env, check=True,
                                                   capture_output=True, text=True).stdout.strip()
                run('init', '--quiet')
                run('commit', '--quiet', '--allow-empty', '-m', 'trial commit')  # Signing would fail.
                self.assertEqual(run('log', '-1', '--format=%an <%ae>|%cn <%ce>'),
                                 '{0} <{1}>|{0} <{1}>'.format(*GIT_IDENTITY))
                self.assertEqual(run('cat-file', 'commit', 'HEAD').count('gpgsig'), 0)

    def test_fixture_git_ignores_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hostile = hostile_git_config(root)
            with mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(hostile), 'HOME': str(root)}):
                checks.init_repo(root / 'project', branch='main')
                (root / 'project/a.txt').write_text('a\n')
                checks.commit_all(root / 'project', 'feat: add a')
                self.assertEqual(checks.git(root / 'project', 'log', '-1', '--format=%an'), GIT_IDENTITY[0])
                with self.assertRaises(subprocess.CalledProcessError):  # The user alias is not visible.
                    checks.git(root / 'project', 'config', '--get', 'alias.st')


def hostile_git_config(root):
    """A user config that fails every commit: required signing with a failing program."""
    config = root / 'user.gitconfig'
    config.write_text('[user]\n\tname = Real User\n\temail = real@example.invalid\n'
                      '[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n'
                      '[alias]\n\tst = status\n')
    return config


class CodexAdapterTests(unittest.TestCase):
    def test_sandbox_writes_only_the_project_and_its_git_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Trial(Path(tmp), Path('/work/project'), Path('/plugin'), 'p', 'm', 'low', 60)
            command = codex.ADAPTER.command(t)
            self.assertEqual(command[command.index('--sandbox') + 1], 'workspace-write')
            self.assertEqual(command[command.index('-C') + 1], '/work/project')
            roots = [value for value in command if value.startswith('sandbox_workspace_write.writable_roots=')]
            self.assertEqual(roots, ['sandbox_workspace_write.writable_roots=["/work/project/.git"]'])
            for setting in ('network_access=false', 'exclude_tmpdir_env_var=true', 'exclude_slash_tmp=true'):
                self.assertIn('sandbox_workspace_write.' + setting, command)

    def test_private_home_holds_a_login_copy_and_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'shared/auth.json'
            source.parent.mkdir()
            source.write_text('{"token": "old"}')
            (root / 'plugin/skills').mkdir(parents=True)
            (root / 'project').mkdir()
            t = Trial(root / 'trial', root / 'project', root / 'plugin', 'p', 'm', 'low', 60)
            t.directory.mkdir()
            with mock.patch.object(codex, 'homes', lambda: root / 'homes'), \
                    mock.patch.object(codex, '_source_auth', return_value=source):
                installed = codex.ADAPTER.prepare(t)
                self.assertEqual(installed, root / 'project/.agents')
                home = t.state['home']
                self.assertEqual((home / 'codex/auth.json').read_text(), '{"token": "old"}')
                env = codex.ADAPTER.environment(t, {})
                self.assertEqual(env['HOME'], str(home / 'user'))
                (home / 'codex/auth.json').write_text('{"token": "new"}')
                audit = codex.ADAPTER.release(t, threading.Lock())
            self.assertEqual(audit, {'auth_refreshed': True, 'auth_carried_back': True})
            self.assertEqual(source.read_text(), '{"token": "new"}')
            self.assertFalse(home.exists())

    def test_refreshed_login_does_not_overwrite_a_newer_shared_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'auth.json'
            source.write_text('a')
            (root / 'plugin').mkdir()
            (root / 'project').mkdir()
            t = Trial(root / 'trial', root / 'project', root / 'plugin', 'p', 'm', 'low', 60)
            with mock.patch.object(codex, 'homes', lambda: root / 'homes'), \
                    mock.patch.object(codex, '_source_auth', return_value=source):
                codex.ADAPTER.prepare(t)
                (t.state['home'] / 'codex/auth.json').write_text('b')
                source.write_text('c')  # Another process refreshed the shared login.
                audit = codex.ADAPTER.release(t, threading.Lock())
            self.assertFalse(audit['auth_carried_back'])
            self.assertEqual(source.read_text(), 'c')

    def test_child_rollouts_count_as_delegation_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / 'sessions/2026/11/15'
            sessions.mkdir(parents=True)
            child = [{'type': 'session_meta', 'payload': {'parent_thread_id': 't-parent'}},
                     {'type': 'event', 'payload': {'type': 'item_completed', 'item': {
                         'command': ['bash', '-lc', 'cat .agents/skills/review-change/SKILL.md']}}},
                     {'type': 'event', 'payload': {'type': 'item_completed', 'item': {
                         'changes': {'notes.md': {}}}}}]
            (sessions / 'child.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in child))
            (sessions / 'parent.jsonl').write_text('{"type":"session_meta","payload":{}}\n')
            found = codex.child_traces(tmp)
        self.assertEqual(found['sessions'], 1)
        self.assertEqual(found['file_changes'], ['notes.md'])
        self.assertEqual(found['skills'], ['.agents/skills/review-change/SKILL.md'])


if __name__ == '__main__':
    unittest.main()
