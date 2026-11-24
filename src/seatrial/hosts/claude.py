"""Claude Code adapter: `claude -p --output-format stream-json` with an inline plugin.

Isolation: no setting sources, no MCP servers except the case's stubs, no session
persistence, a sandbox that denies network and reads of the evaluated project, and a fixed
tool list that adds only the stubs' tools. Scheduling tools
(ScheduleWakeup, CronCreate, Monitor) are not offered, so a trial cannot schedule
extra turns after its report. Background tasks are disabled: an auto-backgrounded
shell command kept the CLI alive after its final result until the trial timed out. The plugin loads from a mounted copy with --plugin-dir;
the NONE revision passes no --plugin-dir.
With ANTHROPIC_API_KEY set, each trial gets its own CLAUDE_CONFIG_DIR, so user-level
skills cannot load. A subscription login lives in the user's config, so trials signed in
that way keep it, and `foreign_skills` reports any skill from outside the plugin.
Delegated sessions stream into the same trace, marked with parent_tool_use_id, so
`tools` holds parent and delegated calls in the order the trace shows them.
"""
import json
import re
from pathlib import Path
import shutil
import subprocess

from seatrial.ports import HostAdapter, new_evidence
from seatrial import config, stub_mcp

TOOLS = 'Read,Write,Edit,Glob,Grep,Skill,Agent,Bash'
EDIT_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
DELEGATE_TOOLS = {'Agent', 'Task'}
# Session variables of a calling Claude Code process must not leak into the trial.
SESSION_VARIABLES = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT')


class Claude(HostAdapter):
    name = 'claude'
    default_model = 'claude-haiku-5-5'
    supports_no_plugin = True
    pass_env = ('ANTHROPIC_*', 'CLAUDE_CODE_*', 'CLAUDE_CONFIG_DIR', 'AWS_*', 'CLOUD_ML_REGION',
                'GOOGLE_APPLICATION_CREDENTIALS', 'VERTEX_REGION_*')

    def version(self):
        try:
            return subprocess.run(['claude', '--version'], capture_output=True, text=True,
                                  check=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def invocation(self, skill):
        return f'/{config.current().namespace}:{skill}'

    def prepare(self, trial):
        servers = {name: {'type': 'stdio', 'command': command, 'args': args}
                   for name, (command, args) in stub_mcp.launch(trial).items()}
        (trial.directory / 'mcp.json').write_text(json.dumps({'mcpServers': servers}) + '\n')
        return trial.plugin

    def command(self, trial):
        allowed = ','.join([TOOLS, *(f"mcp__{stub['name']}" for stub in trial.mcp)])
        settings = {'sandbox': {'enabled': True, 'allowUnsandboxedCommands': False,
                                'network': {'allowedDomains': []},
                                'filesystem': {'denyRead': [str(config.current().root)]}}}
        command = ['claude', '-p', *(['--plugin-dir', str(trial.plugin)] if trial.plugin else []),
                   '--setting-sources', '', '--strict-mcp-config',
                   '--mcp-config', str(trial.directory / 'mcp.json'),
                   '--settings', json.dumps(settings), '--permission-mode', 'acceptEdits',
                   '--permission-prompts', 'none', '--tools', TOOLS, '--allowedTools', allowed,
                   '--output-format', 'stream-json', '--verbose', '--no-session-persistence',
                   '--model', trial.model, '--effort', trial.effort]
        if trial.budget_usd:
            command += ['--max-budget-usd', f'{trial.budget_usd:.2f}']
        return command + [trial.prompt]

    def environment(self, trial, base):
        env = super().environment(trial, base)
        env['NODE_COMPILE_CACHE'] = env['TMPDIR']
        env['CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'] = '1'
        for key in SESSION_VARIABLES:
            env.pop(key, None)
        if env.get('ANTHROPIC_API_KEY'):
            private = trial.directory / 'claude-config'
            private.mkdir(exist_ok=True)
            env['CLAUDE_CONFIG_DIR'] = str(private)
        return env

    def release(self, trial, lock):
        shutil.rmtree(trial.directory / 'claude-config', ignore_errors=True)
        return {}

    def foreign_skills(self, trial, evidence):
        prefix = config.current().namespace + ':'
        plugin = Path(trial.plugin).resolve()
        foreign = []
        for skill in evidence['skills']:
            if skill.endswith('SKILL.md'):
                path = Path(skill).resolve()
                if path != plugin and plugin not in path.parents:
                    foreign.append(skill)
            elif skill and not skill.startswith(prefix):
                foreign.append(skill)
        return foreign

    def collect(self, trial):
        return parse_stream(trial.trace, trial.project, trial.prompt)


def _relative(path, project):
    try:
        return str(Path(path).resolve().relative_to(Path(project).resolve()))
    except (ValueError, OSError):
        return str(path)


def parse_stream(path, project='.', prompt=''):
    """Commands, edits, tool calls, delegations, usage, and errors from a stream-json trace.

    The final message is the last `result` event: a run that waits for a background
    subagent emits one result per turn, and only the last one ends the process.
    A prompt that starts with `/<skill>` expands that skill without a Skill call, so the
    skill counts as loaded when the init event lists it as a slash command."""
    out = new_evidence() | {'tools': []}
    path = Path(path)
    if not path.exists():
        return out
    delegated, traced, results, slash = set(), set(), [], set()
    tokens = {}
    for line in path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type')
        parent = event.get('parent_tool_use_id')
        if kind == 'system' and event.get('subtype') == 'init':
            slash = set(event.get('slash_commands') or [])
        elif kind == 'assistant':
            if parent:
                traced.add(parent)
            for block in (event.get('message') or {}).get('content') or []:
                if not isinstance(block, dict) or block.get('type') != 'tool_use':
                    continue
                name, data = block.get('name'), block.get('input') or {}
                out['tools'].append({'name': str(name or ''), 'input': data if isinstance(data, dict) else {}})
                if name == 'Bash':
                    out['commands'].append(str(data.get('command', '')))
                elif name in EDIT_TOOLS:
                    target = data.get('file_path') or data.get('notebook_path')
                    if target:
                        out['file_changes'].append(_relative(target, project))
                elif name in DELEGATE_TOOLS:
                    delegated.add(block.get('id'))
                elif name == 'Skill':
                    out['skills'].append(str(data.get('skill') or data.get('command') or ''))
                elif name == 'Read' and str(data.get('file_path', '')).endswith('SKILL.md'):
                    out['skills'].append(str(data['file_path']))
        elif kind == 'result':
            results.append(event)
            for model, usage in (event.get('model_usage_since_last_result') or {}).items():
                entry = tokens.setdefault(model, {})
                for key, value in usage.items():
                    if isinstance(value, (int, float)) and key != 'contextWindow' and key != 'maxOutputTokens':
                        entry[key] = entry.get(key, 0) + value
    if results:
        last = results[-1]
        out['final_message'] = last.get('result')
        out['terminal'] = last.get('subtype') == 'success' and not last.get('is_error', False)
        out['cost_usd'] = last.get('total_cost_usd')
        out['usage'] = {'models': tokens, 'turns': len(results), 'num_turns': last.get('num_turns')}
        for event in results:
            if event.get('is_error') or event.get('subtype') != 'success':
                out['errors'].append(json.dumps({k: event.get(k) for k in
                                                 ('subtype', 'is_error', 'api_error_status', 'result')})[:500])
    invoked = re.match(r'/(\S+)', prompt or '')
    if invoked and invoked.group(1) in slash:
        out['skills'].insert(0, invoked.group(1))
    out['delegations'] = len(delegated)
    out['delegation_traces'] = len(delegated & traced)
    return out


ADAPTER = Claude()
