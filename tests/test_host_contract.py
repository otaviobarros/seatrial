"""Contract tests that every registered host adapter must pass.

Each adapter needs recorded traces in `src/seatrial/hosts/samples/<host>/`: `<name>.jsonl`
and `<name>.expected.json`, which lists evidence values and the expected status. Optional
keys: `foreign_skills` and `unknown_skills` (provenance), `trial_project` (the project path),
`trial_prompt` (the prompt the trial sent), `trial_plugin` (the plugin path; null for a
NONE trial), and `trial_mcp_log` (stub MCP log entries the trial recorded).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock

from seatrial import hosts
from seatrial.hosts import codex
from seatrial import checks, runner, stub_mcp
from tests.support import ProjectCase
from seatrial.ports import GIT_IDENTITY, HostAdapter, Trial, validate_evidence

SAMPLES = Path(hosts.__file__).parent / 'samples'


def trial(directory, prompt='Check the change.', project='/project', plugin='/plugin'):
    return Trial(directory, Path(project), plugin and Path(plugin), prompt, 'model-x', 'low', 60)


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
                prompt = expected.pop('trial_prompt', 'Check the change.')
                plugin = expected.pop('trial_plugin', '/plugin')
                stub_log = expected.pop('trial_mcp_log', [])
                with self.subTest(host=name, sample=sample.name), tempfile.TemporaryDirectory() as tmp:
                    t = trial(Path(tmp), prompt=prompt, project=project, plugin=plugin)
                    t.mcp = [{'name': n, 'responses': Path('/responses.json')}
                             for n in dict.fromkeys(e['server'] for e in stub_log)]
                    for entry in stub_log:
                        log = stub_mcp.log_path(t, entry['server'])
                        log.parent.mkdir(exist_ok=True)
                        with log.open('a') as out:
                            out.write(json.dumps(entry) + '\n')
                    shutil.copy(sample, t.trace)
                    evidence = stub_mcp.attach(t, adapter.collect(t))
                    self.assertEqual(validate_evidence(evidence), [])
                    for key, value in expected.items():
                        self.assertEqual(evidence[key], value, key)
                    found = runner.isolation_findings(adapter, t, evidence)
                    if foreign is not None:
                        self.assertEqual(found.get('foreign_skills', []), sorted(set(foreign)))
                    self.assertEqual(found.get('unknown_skills', []), sorted(set(unknown)))
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

    def test_auto_trigger_sends_the_request_alone(self):
        for name, adapter in hosts.available().items():
            with self.subTest(host=name):
                explicit = runner.build_prompt(adapter, 'demo', 'Check it.', Path('/plugin'), 'explicit')
                self.assertEqual(explicit, adapter.invocation('demo') + '\nCheck it.')
                self.assertEqual(runner.build_prompt(adapter, 'demo', 'Check it.', Path('/plugin'), 'auto'), 'Check it.')
                self.assertEqual(runner.build_prompt(adapter, 'demo', 'Check it.', None, 'explicit'), 'Check it.')

    def test_no_skill_trial_loads_no_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'project').mkdir()
            t = Trial(root / 'trial', root / 'project', None, 'Check it.', 'model-x', 'low', 60)
            t.directory.mkdir()
            self.assertNotIn('--plugin-dir', hosts.get('claude').command(t))
            evidence = {'skills': ['demo-plugin:demo'], 'commands': []}
            for adapter in hosts.available().values():
                self.assertTrue(adapter.supports_no_plugin, adapter.name)
                self.assertTrue(adapter.supports_mcp, adapter.name)
                self.assertEqual(runner.isolation_findings(adapter, t, evidence),
                                 {'foreign_skills': ['demo-plugin:demo']})
            with mock.patch.object(codex, 'homes', lambda: root / 'homes'), \
                    mock.patch.object(codex, '_source_auth', return_value=root / 'missing.json'):
                self.assertIsNone(codex.ADAPTER.prepare(t))
                shutil.rmtree(t.state['home'])
            self.assertFalse((root / 'project/.agents').exists())
            read = {'skill_reads': [{'path': '/x/.agents/skills/demo/SKILL.md', 'cwd': '.'}]}
            self.assertEqual(codex.ADAPTER.foreign_skills(t, read), ['/x/.agents/skills/demo/SKILL.md'])

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

    def test_skipped_cd_surfaces_an_isolation_warning(self):
        from seatrial import report, runner
        adapter = hosts.get('codex')
        with tempfile.TemporaryDirectory() as tmp:
            t = trial(Path(tmp))
            shutil.copy(SAMPLES / 'codex/skipped-cd.jsonl', t.trace)
            found = runner.isolation_findings(adapter, t, adapter.collect(t))
        self.assertEqual(found, {'unknown_skills': ['demo/SKILL.md']})
        record = {'status': 'completed', 'wall_s': 1, 'assertions': [], **found}
        case = types.SimpleNamespace(assertions={'primary': 'rate'}, primary='primary')
        self.assertEqual(report.summarize_arm(case, [record])['isolation_warnings'], 1)

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
                         'changes': {'notes.md': {}}}}},
                     {'type': 'event', 'payload': {'type': 'item_completed', 'item': {
                         'type': 'McpToolCall', 'server': 'tracker', 'tool': 'get_issue',
                         'arguments': {'id': '7'}, 'status': 'failed'}}}]
            (sessions / 'child.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in child))
            (sessions / 'parent.jsonl').write_text('{"type":"session_meta","payload":{}}\n')
            found = codex.child_traces(tmp)
        self.assertEqual(found['sessions'], 1)
        self.assertEqual(found['tool_errors'], 1)
        self.assertEqual(found['file_changes'], ['notes.md'])
        self.assertEqual(found['skills'], ['.agents/skills/review-change/SKILL.md'])
        self.assertEqual([call for _, call in found['tools']], [
            {'name': 'shell', 'input': {'command': "bash -lc 'cat .agents/skills/review-change/SKILL.md'"}},
            {'name': 'apply_patch', 'input': {'paths': ['notes.md']}},
            {'name': 'mcp__tracker__get_issue', 'input': {'id': '7'}}])

    def test_rollout_timestamps_interleave_delegated_calls(self):
        # Rollout shapes follow a recorded `codex exec` parent and child rollout pair.
        def line(stamp, item):
            return {'timestamp': stamp, 'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': item}}

        def done(item):
            return {'type': 'item.completed', 'item': item}
        stream = [done({'id': 'i1', 'type': 'command_execution', 'command': "/bin/zsh -lc 'make check'"}),
                  done({'id': 'i2', 'type': 'file_change', 'changes': [{'path': 'notes.md'}]}),
                  done({'id': 'i3', 'type': 'web_search', 'query': 'parent search'}),
                  done({'id': 'i4', 'type': 'collab_tool_call', 'tool': 'wait', 'prompt': None,
                        'receiver_thread_ids': ['t-child']})]
        parent = [{'timestamp': 'T0', 'type': 'session_meta', 'payload': {'id': 't-parent'}},
                  line('2026-11-15T22:22:54.442Z', {'type': 'CommandExecution', 'id': 'exec-1',
                                                    'command': ['/bin/zsh', '-lc', 'make check']}),
                  line('2026-11-15T22:23:12.876Z', {'type': 'SubAgentActivity', 'kind': 'started',
                                                    'agent_path': '/root/review'}),
                  line('2026-11-15T22:23:18.905Z', {'type': 'FileChange', 'id': 'exec-2',
                                                    'changes': {'/abs/notes.md': {'type': 'update'}}}),
                  line('2026-11-15T22:23:34.137Z', {'type': 'CollabAgentToolCall', 'tool': 'wait',
                                                    'receiver_thread_ids': ['t-child']})]
        child = [{'timestamp': 'T0', 'type': 'session_meta', 'payload': {'parent_thread_id': 't-parent'}},
                 line('2026-11-15T22:23:19.552Z', {'type': 'McpToolCall', 'server': 'tracker',
                                                   'tool': 'get_issue', 'arguments': {'key': 'DEMO-1'}}),
                 line('2026-11-15T22:23:20.000Z', {'type': 'Extension', 'kind': 'web.search',
                                                   'query': 'release notes'})]
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / 'codex/sessions/2026/11/15'
            sessions.mkdir(parents=True)
            for name, lines in (('parent.jsonl', parent), ('child.jsonl', child), ('stream.jsonl', stream)):
                (sessions.parent if name == 'stream.jsonl' else sessions).joinpath(name).write_text(
                    ''.join(json.dumps(e) + '\n' for e in lines))
            trial = types.SimpleNamespace(trace=sessions.parent / 'stream.jsonl', state={'home': Path(tmp)})
            found = codex.Codex().collect(trial)
        # The parent's calls keep their stream inputs; the parent's web search has no rollout
        # entry, so it keeps the file change's time and stays before the delegated calls.
        self.assertEqual(found['tools'], [
            {'name': 'shell', 'input': {'command': "/bin/zsh -lc 'make check'"}},
            {'name': 'apply_patch', 'input': {'paths': ['notes.md']}},
            {'name': 'web_search', 'input': {'query': 'parent search'}},
            {'name': 'mcp__tracker__get_issue', 'input': {'key': 'DEMO-1'}},
            {'name': 'web_search', 'input': {'query': 'release notes'}},
            {'name': 'wait', 'input': {'receiver_thread_ids': ['t-child']}}])

    def test_without_a_parent_rollout_delegated_calls_follow_the_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / 'codex/sessions'
            sessions.mkdir(parents=True)
            (sessions / 'child.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
                {'type': 'session_meta', 'payload': {'parent_thread_id': 't-parent'}},
                {'timestamp': 'T1', 'payload': {'type': 'item_completed', 'item': {'command': 'make check'}}}]))
            trial = types.SimpleNamespace(trace=SAMPLES / 'codex/delegated-report.jsonl', state={'home': Path(tmp)})
            found = codex.Codex().collect(trial)
        self.assertEqual(found['tools'][-1], {'name': 'shell', 'input': {'command': 'make check'}})
        self.assertEqual(found['tools'][-2]['name'], 'wait')

    def test_invalid_tool_entries_fail_the_evidence_contract(self):
        evidence = codex.parse_stream('/missing')
        evidence['tools'] = [{'name': 'Bash', 'input': 'make check'}]
        self.assertIn('tools must hold {"name": str, "input": dict} entries', validate_evidence(evidence))

    def test_a_codex_call_counts_once_and_only_when_it_completes(self):
        item = {'type': 'command_execution', 'command': 'make check'}
        with tempfile.TemporaryDirectory() as folder:
            stream = Path(folder, 'stream.jsonl')
            stream.write_text(''.join(json.dumps({'type': kind, 'item': item}) + '\n'
                                      for kind in ('item.started', 'item.completed')))
            self.assertEqual(codex.parse_stream(stream)['tools'],
                             [{'name': 'shell', 'input': {'command': 'make check'}}])
            stream.write_text(json.dumps({'type': 'item.started', 'item': item}) + '\n')
            cut_off = codex.parse_stream(stream)
        self.assertEqual((cut_off['tools'], cut_off['commands']), ([], []))

    def test_evidence_without_tools_is_valid(self):
        evidence = codex.parse_stream('/missing')
        del evidence['tools']
        self.assertEqual(validate_evidence(evidence), [])

    def test_tool_errors_must_be_a_count(self):
        evidence = codex.parse_stream('/missing')
        for value, problems in ((0, []), (3, []), (True, ['tool_errors must be a count of 0 or more']),
                                (-1, ['tool_errors must be a count of 0 or more'])):
            evidence['tool_errors'] = value
            self.assertEqual(validate_evidence(evidence), problems)


if __name__ == '__main__':
    unittest.main()
