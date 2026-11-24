"""Stub MCP servers that a case declares under `[[mcp]]` in case.toml.

The server half runs as a script: `python -I stub_mcp.py <name> <responses> <log>`. It
speaks JSON-RPC 2.0 over stdio, one message per line, lists the tools in the response
file, answers each call with the first canned response whose `match` fits the input,
and appends every call to the log. It imports only the standard library, so it runs
from its file path in any trial environment.

The harness half builds the host entries for a trial and copies the call log into
evidence. A response file maps tool names to tools:

    {"get_issue": {"description": "Read one issue.",
                   "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
                   "responses": [{"match": {"id": "^42$"}, "result": {"title": "Login fails"}},
                                 {"result": "No such issue.", "is_error": true}]}}
"""
import json
from pathlib import Path
import re
import sys

SERVER_NAME = re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$')  # No `__`: tool names are mcp__<server>__<tool>.
TOOL_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
PROTOCOL = '2025-06-18'
TOOL_KEYS = {'description', 'input_schema', 'responses'}
RESPONSE_KEYS = {'match', 'result', 'is_error'}


def load_responses(path):
    """Read and validate a response file; raise ValueError naming the first problem."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f'{path}: {error}') from None
    if not isinstance(data, dict) or not data:
        raise ValueError(f'{path}: must be a JSON object that maps tool names to tools')
    for name, tool in data.items():
        where = f'{path}: tool {name!r}'
        if not TOOL_NAME.match(name):
            raise ValueError(f'{where}: names use letters, digits, _ and -, up to 64 characters')
        if not isinstance(tool, dict) or set(tool) - TOOL_KEYS:
            raise ValueError(f'{where}: must be an object with only {sorted(TOOL_KEYS)}')
        if not isinstance(tool.get('description', ''), str):
            raise ValueError(f'{where}: description must be a string')
        schema = tool.get('input_schema', {})
        if not isinstance(schema, dict) or (schema and schema.get('type') != 'object'):
            raise ValueError(f'{where}: input_schema must be a JSON Schema object with "type": "object"')
        responses = tool.get('responses')
        if not isinstance(responses, list) or not responses:
            raise ValueError(f'{where}: responses must be a non-empty list')
        for index, response in enumerate(responses):
            if not isinstance(response, dict) or 'result' not in response or set(response) - RESPONSE_KEYS:
                raise ValueError(f'{where}: response {index} needs result and only {sorted(RESPONSE_KEYS)}')
            match = response.get('match', {})
            if not isinstance(match, dict) or not all(isinstance(v, str) for v in match.values()):
                raise ValueError(f'{where}: response {index} match must map input fields to patterns')
            for pattern in match.values():
                try:
                    re.compile(pattern)
                except re.error as error:
                    raise ValueError(f'{where}: response {index} pattern {pattern!r}: {error}') from None
            if not isinstance(response.get('is_error', False), bool):
                raise ValueError(f'{where}: response {index} is_error must be true or false')
    return data


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _fits(match, arguments):
    """Each named input field exists and its text contains a match for the pattern."""
    return all(field in arguments and re.search(pattern, _text(arguments[field]))
               for field, pattern in match.items())


def answer(tools, name, arguments):
    """(MCP tool result, matched) for one call."""
    tool = tools.get(name)
    response = None
    if tool is not None:
        response = next((r for r in tool['responses'] if _fits(r.get('match', {}), arguments)), None)
    if response is None:
        # The agent reads this text; it must not reveal that the server is a stub.
        reason = 'unknown tool' if tool is None else 'no result for this input'
        return {'content': [{'type': 'text', 'text': f'{name}: {reason}'}], 'isError': True}, False
    value = response['result']
    result = {'content': [{'type': 'text', 'text': _text(value)}],
              'isError': response.get('is_error', False)}
    if isinstance(value, dict):
        result['structuredContent'] = value
    return result, True


def listing(tools):
    return [{'name': name, 'description': tool.get('description', ''),
             'inputSchema': tool.get('input_schema') or {'type': 'object'}}
            for name, tool in tools.items()]


def handle(message, tools, server, log):
    """The JSON-RPC response for one request, or None for a notification."""
    method, ident = message.get('method'), message.get('id')
    params = message.get('params') if isinstance(message.get('params'), dict) else {}
    if 'id' not in message:
        return None
    if method == 'initialize':
        result = {'protocolVersion': params.get('protocolVersion') or PROTOCOL,
                  'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': server, 'version': '1'}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': listing(tools)}
    elif method == 'tools/call':
        name = str(params.get('name', ''))
        arguments = params.get('arguments') if isinstance(params.get('arguments'), dict) else {}
        result, matched = answer(tools, name, arguments)
        with open(log, 'a') as out:
            out.write(json.dumps({'server': server, 'tool': name, 'input': arguments,
                                      'matched': matched, 'is_error': result['isError']}) + '\n')
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': f'unknown method {method}'}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def serve(server, responses, log, stdin=sys.stdin, stdout=sys.stdout):
    tools = load_responses(responses)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        else:
            reply = handle(message, tools, server, log) if isinstance(message, dict) else \
                {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
        if reply is not None:
            stdout.write(json.dumps(reply) + '\n')
            stdout.flush()


# Harness side: these run in the seatrial process, never in the stub.

def log_path(trial, name):
    return trial.directory / 'mcp' / f'{name}.jsonl'


def launch(trial):
    """{server name: (command, args)} for the stubs a trial declares."""
    return {stub['name']: (sys.executable, ['-I', str(Path(__file__).resolve()), stub['name'],
                                            str(stub['responses']), str(log_path(trial, stub['name']))])
            for stub in trial.mcp}


def attach(trial, evidence):
    """Copy stub call logs into evidence `mcp_calls` and mark the stubs' calls in `tools`.

    A trace call named `mcp__<server>__<tool>` gets `"server": <server>`. Evidence without
    `tools` stays without it: the stub log cannot show the host's other calls."""
    if not trial.mcp:
        return evidence
    calls = []
    for stub in trial.mcp:
        path = log_path(trial, stub['name'])
        lines = path.read_text(errors='replace').splitlines() if path.exists() else []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and isinstance(entry.get('input'), dict):
                calls.append(entry)
    evidence['mcp_calls'] = calls
    for call in evidence.get('tools', []):
        name = call.get('name') if isinstance(call, dict) else None
        for stub in trial.mcp:
            if isinstance(name, str) and name.startswith(f"mcp__{stub['name']}__"):
                call['server'] = stub['name']
    return evidence


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit('usage: stub_mcp.py <name> <responses.json> <log.jsonl>')
    serve(*sys.argv[1:])
