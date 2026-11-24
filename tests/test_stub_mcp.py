"""The stub MCP server, its response files, case declarations, and host configuration."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from seatrial import cases, hosts, stub_mcp
from seatrial.hosts import codex
from seatrial.ports import Trial, validate_evidence
from tests.support import ProjectCase
from tests.test_cases import GOOD_TOML, write_case

RESPONSES = {
    'get_issue': {
        'description': 'Read one issue.',
        'input_schema': {'type': 'object', 'properties': {'id': {'type': 'string'}}},
        'responses': [{'match': {'id': '^42$'}, 'result': {'id': '42', 'title': 'Login fails'}},
                      {'match': {'id': '^7$'}, 'result': 'No such issue.', 'is_error': True}]},
    'add_comment': {'responses': [{'result': 'ok'}]},
}


def converse(responses, log, *messages):
    """Run the stub as a host would, and return its replies by id."""
    command = [*stub_mcp_command('tracker', responses, log)]
    lines = ''.join(json.dumps(m) + '\n' for m in messages)
    done = subprocess.run(command, input=lines, capture_output=True, text=True, timeout=30, check=True)
    return {r['id']: r for r in map(json.loads, done.stdout.splitlines())}


def stub_mcp_command(name, responses, log):
    trial = Trial(Path(log).parent, Path('/project'), None, '', 'm', 'low', 1,
                  mcp=[{'name': name, 'responses': responses}])
    command, args = stub_mcp.launch(trial)[name]
    args[-1] = str(log)
    return [command, *args]


def call(ident, name, arguments):
    return {'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}


class StubServerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.responses = self.root / 'responses.json'
        self.responses.write_text(json.dumps(RESPONSES))
        self.log = self.root / 'mcp' / 'tracker.jsonl'

    def test_initialize_and_tool_listing(self):
        replies = converse(self.responses, self.log,
                           {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                            'params': {'protocolVersion': '2025-03-26'}},
                           {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                           {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                           {'jsonrpc': '2.0', 'id': 3, 'method': 'resources/list'})
        self.assertEqual(sorted(replies), [1, 2, 3])  # The notification gets no reply.
        self.assertEqual(replies[1]['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(replies[1]['result']['serverInfo']['name'], 'tracker')
        tools = {t['name']: t for t in replies[2]['result']['tools']}
        self.assertEqual(set(tools), {'get_issue', 'add_comment'})
        self.assertEqual(tools['get_issue']['inputSchema']['properties'], {'id': {'type': 'string'}})
        self.assertEqual(tools['add_comment']['inputSchema'], {'type': 'object'})
        self.assertEqual(replies[3]['error']['code'], -32601)
        self.assertFalse(self.log.exists())

    def test_matched_call_returns_the_first_fitting_response(self):
        replies = converse(self.responses, self.log, call(1, 'get_issue', {'id': '42'}),
                           call(2, 'get_issue', {'id': '7'}), call(3, 'add_comment', {'body': 'x'}))
        self.assertEqual(replies[1]['result']['structuredContent'], {'id': '42', 'title': 'Login fails'})
        self.assertEqual(json.loads(replies[1]['result']['content'][0]['text'])['title'], 'Login fails')
        self.assertFalse(replies[1]['result']['isError'])
        self.assertEqual(replies[2]['result']['content'], [{'type': 'text', 'text': 'No such issue.'}])
        self.assertTrue(replies[2]['result']['isError'])
        self.assertEqual(replies[3]['result']['content'][0]['text'], 'ok')

    def test_unmatched_call_returns_an_error_result(self):
        replies = converse(self.responses, self.log, call(1, 'get_issue', {'id': '9'}),
                           call(2, 'get_issue', {}), call(3, 'delete_issue', {'id': '42'}))
        for ident in (1, 2, 3):
            self.assertTrue(replies[ident]['result']['isError'], ident)
        self.assertIn('no result for this input', replies[1]['result']['content'][0]['text'])
        self.assertIn('unknown tool', replies[3]['result']['content'][0]['text'])
        self.assertNotIn('stub', json.dumps(replies).lower())

    def test_every_call_is_logged_in_order(self):
        converse(self.responses, self.log, call(1, 'get_issue', {'id': '42'}), call(2, 'delete_issue', {}))
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(entries, [
            {'server': 'tracker', 'tool': 'get_issue', 'input': {'id': '42'}, 'matched': True, 'is_error': False},
            {'server': 'tracker', 'tool': 'delete_issue', 'input': {}, 'matched': False, 'is_error': True}])

    def test_bad_lines_get_json_rpc_errors(self):
        done = subprocess.run(stub_mcp_command('tracker', self.responses, self.log),
                              input='not json\n[1]\n', capture_output=True, text=True, timeout=30, check=True)
        codes = [json.loads(line)['error']['code'] for line in done.stdout.splitlines()]
        self.assertEqual(codes, [-32700, -32600])

    def test_response_file_problems_are_named(self):
        bad = {
            'maps tool names': [],
            'names use letters': {'bad name': {'responses': [{'result': 1}]}},
            'only': {'t': {'responses': [{'result': 1}], 'extra': 1}},
            'type": "object"': {'t': {'input_schema': {'properties': {}}, 'responses': [{'result': 1}]}},
            'non-empty list': {'t': {'responses': []}},
            'needs result': {'t': {'responses': [{'match': {}}]}},
            'map input fields': {'t': {'responses': [{'match': {'id': 4}, 'result': 1}]}},
            'pattern': {'t': {'responses': [{'match': {'id': '('}, 'result': 1}]}},
            'is_error': {'t': {'responses': [{'result': 1, 'is_error': 'yes'}]}},
        }
        for reason, data in bad.items():
            with self.subTest(reason=reason):
                self.responses.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, reason):
                    stub_mcp.load_responses(self.responses)


class AttachTests(unittest.TestCase):
    def trial(self, tmp, *names):
        return Trial(Path(tmp), Path('/project'), None, '', 'm', 'low', 1,
                     mcp=[{'name': n, 'responses': Path('/r.json')} for n in names])

    def write_log(self, trial, name, *entries):
        path = stub_mcp.log_path(trial, name)
        path.parent.mkdir(exist_ok=True)
        path.write_text(''.join(json.dumps(e) + '\n' for e in entries) + 'torn line')

    def test_stub_calls_are_marked_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = self.trial(tmp, 'tracker', 'wiki')
            entry = {'server': 'tracker', 'tool': 'get_issue', 'input': {'id': '1'}, 'matched': True,
                     'is_error': False}
            self.write_log(trial, 'tracker', entry)
            evidence = {'tools': [{'name': 'Bash', 'input': {}}, {'name': 'mcp__tracker__get_issue',
                                                                  'input': {'id': '1'}},
                                  {'name': 'mcp__trackers__x', 'input': {}}]}
            stub_mcp.attach(trial, evidence)
            self.assertEqual([t.get('server') for t in evidence['tools']], [None, 'tracker', None])
            self.assertEqual(evidence['mcp_calls'], [entry])  # The wiki stub was never started.

    def test_logged_calls_do_not_stand_in_for_missing_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = self.trial(tmp, 'tracker')
            self.write_log(trial, 'tracker', {'server': 'tracker', 'tool': 'get_issue', 'input': {'id': '1'},
                                              'matched': True, 'is_error': False})
            evidence = stub_mcp.attach(trial, {})
            self.assertNotIn('tools', evidence)  # Missing tools means not recorded, not none.
            self.assertEqual([c['tool'] for c in evidence['mcp_calls']], ['get_issue'])

    def test_malformed_tool_entries_are_left_to_the_contract_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = stub_mcp.attach(self.trial(tmp, 'tracker'), {'tools': [{'input': {}}, 'x']})
            self.assertEqual(evidence['tools'], [{'input': {}}, 'x'])
            self.assertIn('tools must hold {"name": str, "input": dict} entries', validate_evidence(evidence))

    def test_trials_without_stubs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(stub_mcp.attach(self.trial(tmp), {'tools': []}), {'tools': []})

    def test_evidence_contract_checks_the_server_field(self):
        from seatrial.ports import new_evidence
        evidence = new_evidence() | {'tools': [{'name': 'x', 'input': {}, 'server': 3}]}
        self.assertIn('tools server must be a string', validate_evidence(evidence))
        evidence['tools'][0]['server'] = 'tracker'
        self.assertEqual(validate_evidence(evidence | {'mcp_calls': []}), [])


STUB_TOML = GOOD_TOML + '''
[[mcp]]
name = "tracker"
responses = "tracker.json"
'''


class CaseStubTests(unittest.TestCase):
    def test_declared_stubs_resolve_inside_the_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_case(tmp, STUB_TOML)
            (directory / 'tracker.json').write_text(json.dumps(RESPONSES))
            case = cases.discover(tmp)['demo-001']
            self.assertEqual(case.stubs(), [{'name': 'tracker', 'responses': (directory / 'tracker.json').resolve()}])
            before = case.identity()['grader_sha256']
            (directory / 'tracker.json').write_text(json.dumps({'t': {'responses': [{'result': 1}]}}))
            self.assertNotEqual(cases.discover(tmp)['demo-001'].identity()['grader_sha256'], before)

    def test_cases_without_stubs_keep_their_identity_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp)
            case = cases.discover(tmp)['demo-001']
            self.assertEqual(case.mcp, [])
            self.assertEqual(case.stubs(), [])

    def test_invalid_stub_declarations_are_rejected(self):
        bad = {
            'exactly name and responses': STUB_TOML + '[[mcp]]\nname = "wiki"\n',
            'declared twice': STUB_TOML + '[[mcp]]\nname = "tracker"\nresponses = "tracker.json"\n',
            'lowercase letters': STUB_TOML.replace('"tracker"', '"Tracker__x"'),
            'inside the case folder': STUB_TOML.replace('"tracker.json"', '"../../outside.json"'),
            'mcp \'tracker\'': STUB_TOML.replace('"tracker.json"', '"missing.json"'),
            'mcp must be': GOOD_TOML + 'mcp = "tracker"\n',
        }
        for reason, toml in bad.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                directory = write_case(tmp, toml)
                (directory / 'tracker.json').write_text(json.dumps(RESPONSES))
                (Path(tmp) / 'outside.json').write_text(json.dumps(RESPONSES))
                with self.assertRaisesRegex(ValueError, reason):
                    cases.discover(tmp)


class HostConfigurationTests(ProjectCase, unittest.TestCase):
    def trial(self, *names):
        directory = self.root / 'trial'
        directory.mkdir(exist_ok=True)
        responses = directory / 'responses.json'
        responses.write_text(json.dumps(RESPONSES))
        return Trial(directory, self.root / 'project', Path('/plugin'), 'Check it.', 'model-x', 'low', 60,
                     mcp=[{'name': n, 'responses': responses} for n in names])

    def test_claude_loads_only_the_declared_stubs(self):
        claude = hosts.get('claude')
        for names in ((), ('tracker',), ('tracker', 'wiki')):
            with self.subTest(names=names):
                trial = self.trial(*names)
                claude.prepare(trial)
                servers = json.loads((trial.directory / 'mcp.json').read_text())['mcpServers']
                self.assertEqual(sorted(servers), sorted(names))
                command = claude.command(trial)
                self.assertIn('--strict-mcp-config', command)
                self.assertEqual(command[command.index('--mcp-config') + 1], str(trial.directory / 'mcp.json'))
                allowed = command[command.index('--allowedTools') + 1].split(',')
                self.assertEqual([t for t in allowed if t.startswith('mcp__')], [f'mcp__{n}' for n in names])
                self.assertEqual(command[command.index('--tools') + 1], 'Read,Write,Edit,Glob,Grep,Skill,Agent,Bash')
        server = servers['tracker']
        self.assertEqual(server['args'][-1], str(stub_mcp.log_path(trial, 'tracker')))
        reply = subprocess.run([server['command'], *server['args']], capture_output=True, text=True, timeout=30,
                               input=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n')
        self.assertEqual(len(json.loads(reply.stdout)['result']['tools']), 2)

    def test_codex_loads_only_the_declared_stubs(self):
        for names in ((), ('tracker',), ('tracker', 'wiki')):
            with self.subTest(names=names):
                command = codex.ADAPTER.command(self.trial(*names))
                overrides = [command[i + 1] for i, word in enumerate(command) if word == '-c']
                stubs = [o for o in overrides if o.startswith('mcp_servers.')]
                self.assertEqual(sorted({o.split('.')[1] for o in stubs}), sorted(names))
                self.assertIn('--ignore-user-config', command)
                for name in names:
                    self.assertIn(f'mcp_servers.{name}.default_tools_approval_mode="approve"', stubs)
                    args = next(o for o in stubs if o.startswith(f'mcp_servers.{name}.args='))
                    self.assertEqual(json.loads(args.split('=', 1)[1])[-2:],
                                     [str(self.root / 'trial/responses.json'),
                                      str(stub_mcp.log_path(self.trial(), name))])


if __name__ == '__main__':
    unittest.main()
