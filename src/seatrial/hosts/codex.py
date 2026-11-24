"""Codex CLI adapter: `codex exec --json` with a private CODEX_HOME and HOME.

Isolation: each trial gets its own CODEX_HOME holding a copy of the login, and its
own HOME, so user configuration and user-level skills (~/.agents) never load. The
plugin is copied to `<project>/.agents`, where Codex discovers project skills; the
NONE revision copies nothing. The
sandbox writes only inside the project, including its `.git`; `.agents` stays read-only.
Delegated sessions do not appear in the parent's JSON stream; their rollouts in the
private CODEX_HOME are read before it is deleted, so their tool calls follow the
parent's calls in `tools` rather than interleaving with them.
"""
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess

from seatrial.ports import HostAdapter, new_evidence
from seatrial.workspace import state


def homes():
    return state() / '.homes'


def _source_auth():
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'auth.json'


class Codex(HostAdapter):
    name = 'codex'
    default_model = 'gpt-6-luna'
    supports_no_plugin = True
    pass_env = ('OPENAI_*', 'AZURE_OPENAI_*')

    def version(self):
        try:
            return subprocess.run(['codex', '--version'], capture_output=True, text=True,
                                  check=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def invocation(self, skill):
        return f'${skill}'

    def prepare(self, trial):
        installed = None
        if trial.plugin is not None:
            installed = trial.project / '.agents'
            shutil.copytree(trial.plugin, installed)
        home = homes() / secrets.token_hex(6)
        (home / 'codex').mkdir(parents=True, mode=0o700)
        (home / 'user').mkdir(mode=0o700)
        source = _source_auth()
        original = source.read_bytes() if source.exists() else None
        if original is not None:
            target = home / 'codex/auth.json'
            target.write_bytes(original)
            os.chmod(target, 0o600)
        trial.state.update(home=home, auth_source=source, auth_original=original)
        return installed

    def command(self, trial):
        return ['codex', 'exec', '--ignore-user-config', '--json',
                '--sandbox', 'workspace-write', '--enable', 'multi_agent',
                '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
                '-c', 'sandbox_workspace_write.network_access=false',
                '-c', 'sandbox_workspace_write.exclude_tmpdir_env_var=true',
                '-c', 'sandbox_workspace_write.exclude_slash_tmp=true',
                # Workspace-write keeps <project>/.git read-only; a trial must commit and merge.
                '-c', 'sandbox_workspace_write.writable_roots=' + json.dumps([str(trial.project / '.git')]),
                '-C', str(trial.project), '--model', trial.model,
                '-c', 'model_reasoning_effort=' + json.dumps(trial.effort), trial.prompt]

    def environment(self, trial, base):
        env = super().environment(trial, base)
        env['CODEX_HOME'] = str(trial.state['home'] / 'codex')
        env['HOME'] = str(trial.state['home'] / 'user')
        return env

    def collect(self, trial):
        evidence = parse_stream(trial.trace)
        home = trial.state.get('home')
        if home:
            children = child_traces(home / 'codex')
            evidence['delegation_traces'] = children['sessions']
            evidence['delegations'] = max(evidence['delegations'], children['sessions'])
            evidence['commands'] += children['commands']
            evidence['file_changes'] += children['file_changes']
            evidence['tools'] += children['tools']
            evidence['skills'] += children['skills']
            evidence['skill_reads'] = evidence.get('skill_reads', []) + children['skill_reads']
        return evidence

    def foreign_skills(self, trial, evidence):
        """SKILL.md reads outside the installed plugin."""
        return [read['path'] for read in _reads(evidence) if _origin(trial, read) == 'foreign']

    def unknown_skills(self, trial, evidence):
        """SKILL.md reads by a relative path from a directory the trace does not establish."""
        return [read['path'] for read in _reads(evidence) if _origin(trial, read) == 'unknown']

    def release(self, trial, lock):
        """Carry a refreshed login back only when the shared file is still the one we copied."""
        home = trial.state.get('home')
        audit = {'auth_refreshed': False, 'auth_carried_back': False}
        if not home:
            return audit
        try:
            refreshed_path = home / 'codex/auth.json'
            original, source = trial.state['auth_original'], trial.state['auth_source']
            if original is not None and refreshed_path.exists():
                refreshed = refreshed_path.read_bytes()
                audit['auth_refreshed'] = refreshed != original
                with lock:
                    if refreshed != original and source.read_bytes() == original:
                        tmp = source.with_name(f'.auth-{secrets.token_hex(8)}.tmp')
                        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, 'wb') as handle:
                            handle.write(refreshed)
                        tmp.replace(source)
                        audit['auth_carried_back'] = True
        finally:
            shutil.rmtree(home, ignore_errors=True)  # Never keep a copied login.
        return audit


SKILL_FILE = re.compile(r'[\w./~-]*SKILL\.md')
SEPARATOR = re.compile(r'[;&|()\n]+')
SHELLS = {'sh', 'bash', 'zsh', 'dash'}


def _script(command):
    """The shell script a command runs: the argument after -c or -lc, else the command itself."""
    try:
        words = list(command) if isinstance(command, list) else shlex.split(command)
    except ValueError:
        return None
    for index, word in enumerate(words[:-1]):
        if Path(words[0]).name in SHELLS and word.startswith('-') and word.endswith('c'):
            return words[index + 1]
    return ' '.join(shlex.quote(w) for w in words)


def _skill_reads(command, cwd='.'):
    """SKILL.md paths a command reads, each with the directory it was read from.

    Quoted paths keep their spaces. A literal `cd` holds only for commands joined to it
    by `&&`: after any other separator the `cd` may have failed or been skipped, so the
    directory becomes None. A `cd` whose target the trace cannot show does the same."""
    script = _script(command)
    if script is None:  # Unbalanced quotes: keep the paths, but not where they were read.
        return [{'path': p, 'cwd': None} for p in SKILL_FILE.findall(str(command))]
    lexer = shlex.shlex(script, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return [{'path': p, 'cwd': None} for p in SKILL_FILE.findall(script)]
    reads, stack, start, moved, joined = [], [], True, False, ''
    for index, token in enumerate(tokens):
        if SEPARATOR.fullmatch(token):
            for char in token:
                if char == '(':
                    stack.append((cwd, moved))
                    moved = False
                elif char == ')' and stack:
                    cwd, moved = stack.pop()
            joined = token.replace('(', '').replace(')', '')
            if joined and joined != '&&' and moved:  # The cd may not have run.
                cwd, moved = None, False
            start = True
            continue
        if start and token in ('cd', 'pushd'):
            target = tokens[index + 1] if index + 1 < len(tokens) else None
            literal = target and not SEPARATOR.fullmatch(target) and not re.search(r'[$`*?]|^[~-]', target)
            if literal and cwd is not None and joined not in ('||', '|'):
                cwd, moved = str(Path(cwd) / target), True
            else:
                cwd = None
        elif 'SKILL.md' in token:
            path = token[:token.rindex('SKILL.md') + len('SKILL.md')].rsplit('=', 1)[-1]
            reads.append({'path': path, 'cwd': cwd})
        start = False
    return reads


def _reads(evidence):
    """Recorded reads; evidence saved before directories were kept reads from the project."""
    if 'skill_reads' in evidence:
        return evidence['skill_reads']
    return [{'path': p, 'cwd': '.'} for p in evidence['skills']]


def _origin(trial, read):
    """'installed', 'foreign', or 'unknown' for one SKILL.md read."""
    if trial.plugin is None:  # Nothing was installed: every skill read is foreign.
        return 'foreign'
    if read['path'].startswith('~'):  # HOME is private and empty: never the installed plugin.
        return 'foreign'
    path = Path(read['path'])
    if not path.is_absolute():
        if read['cwd'] is None:
            return 'unknown'
        path = trial.project / read['cwd'] / path
    path = Path(os.path.normpath(path)).resolve()
    roots = [(trial.project / '.agents').resolve(), Path(trial.plugin).resolve()]
    return 'installed' if any(path == r or r in path.parents for r in roots) else 'foreign'


def _add_reads(out, command, cwd):
    reads = _skill_reads(command, cwd if isinstance(cwd, str) and cwd else '.')
    out['skills'] += [r['path'] for r in reads]
    out.setdefault('skill_reads', []).extend(reads)


def tool_call(item):
    """A tool call entry for one Codex item, or None when the item is not a tool call.

    Names follow the Codex item: `shell`, `apply_patch`, `web_search`, the collaboration
    tool (`spawn_agent`, `wait`, ...), and `mcp__<server>__<tool>` for MCP calls."""
    kind = item.get('type')
    if kind == 'mcp_tool_call':
        arguments = item.get('arguments')
        return {'name': f"mcp__{item.get('server', '')}__{item.get('tool', '')}",
                'input': arguments if isinstance(arguments, dict) else {}}
    if kind == 'web_search':
        return {'name': 'web_search', 'input': {'query': str(item.get('query', ''))}}
    if kind == 'collab_tool_call':
        return {'name': str(item.get('tool', '')),
                'input': {k: item[k] for k in ('prompt', 'receiver_thread_ids') if item.get(k) is not None}}
    # Delegated rollouts may omit the item type; the command or changes field identifies the call.
    if kind == 'command_execution' or item.get('command'):
        command = item.get('command', '')
        return {'name': 'shell', 'input': {'command': ' '.join(command) if isinstance(command, list)
                                           else str(command)}}
    if kind == 'file_change' or item.get('changes'):
        found = item.get('changes')
        paths = [str(p) for p in found] if isinstance(found, dict) else \
            [str(c.get('path', '')) for c in found or [] if isinstance(c, dict)]
        return {'name': 'apply_patch', 'input': {'paths': paths}}
    return None


def _add_tool(out, positions, item):
    """Record a call at its first event; a later event for the same item replaces its input."""
    call = tool_call(item)
    if call is None:
        return
    key = item.get('id')
    if key is not None and key in positions:
        out['tools'][positions[key]] = call
        return
    if key is not None:
        positions[key] = len(out['tools'])
    out['tools'].append(call)


def parse_stream(path):
    """Commands, edits, tool calls, delegations, usage, and errors from a `codex exec --json` stream."""
    out = new_evidence() | {'tools': []}
    collab, receivers, spawns, positions = False, set(), 0, {}
    path = Path(path)
    if not path.exists():
        return out
    for line in path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type')
        if kind == 'turn.completed':
            out['terminal'] = True
            out['usage'] = event.get('usage')
        elif kind in ('turn.failed', 'error'):
            out['errors'].append(json.dumps(event)[:500])
        elif kind == 'item.started':
            item = event.get('item') or {}
            if item.get('id') is not None:  # Without an id, the completed event cannot replace it.
                _add_tool(out, positions, item)
        elif kind == 'item.completed':
            item = event.get('item') or {}
            _add_tool(out, positions, item)
            if item.get('type') == 'command_execution':
                command = str(item.get('command', ''))
                out['commands'].append(command)
                _add_reads(out, item.get('command', ''), item.get('cwd'))
            elif item.get('type') == 'file_change':
                out['file_changes'] += [str(c.get('path', '')) for c in item.get('changes') or []
                                        if isinstance(c, dict)]
            elif item.get('type') == 'agent_message':
                out['final_message'] = item.get('text')
            elif item.get('type') == 'collab_tool_call':
                # Spawns may be missing from the stream; any collaboration call means one ran.
                collab = True
                receivers.update(item.get('receiver_thread_ids') or [])
                spawns += item.get('tool') == 'spawn_agent'
    out['delegations'] = max(spawns, len(receivers), 1 if collab else 0)
    return out


def child_traces(codex_home):
    """Commands and file edits from delegated sessions in a private CODEX_HOME."""
    commands, changes, sessions, found_reads = [], [], 0, {'skills': [], 'skill_reads': []}
    calls = []
    for rollout in sorted(Path(codex_home).glob('sessions/**/*.jsonl')):
        lines = rollout.read_text(errors='replace').splitlines()
        if not lines or '"parent_thread_id"' not in lines[0]:
            continue
        sessions += 1
        for line in lines:
            try:
                payload = json.loads(line).get('payload') or {}
            except ValueError:
                continue
            item = payload.get('item') if isinstance(payload, dict) else None
            if payload.get('type') != 'item_completed' or not isinstance(item, dict):
                continue
            call = tool_call(item)
            if call is not None:
                calls.append(call)
            command = item.get('command')
            if command:
                text = ' '.join(command) if isinstance(command, list) else str(command)
                commands.append(text)
                _add_reads(found_reads, command, item.get('cwd'))
            found = item.get('changes')
            if isinstance(found, dict):
                changes += [str(p) for p in found]
            elif isinstance(found, list):
                changes += [str(c.get('path', '')) for c in found if isinstance(c, dict)]
    return {'sessions': sessions, 'commands': commands, 'file_changes': changes, 'tools': calls,
            **found_reads}


ADAPTER = Codex()
