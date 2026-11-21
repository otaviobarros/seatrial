"""Codex CLI adapter: `codex exec --json` with a private CODEX_HOME and HOME.

Isolation: each trial gets its own CODEX_HOME holding a copy of the login, and its
own HOME, so user configuration and user-level skills (~/.agents) never load. The
plugin is copied to `<project>/.agents`, where Codex discovers project skills. The
sandbox writes only inside the project, including its `.git`; `.agents` stays read-only.
Delegated sessions do not appear in the parent's JSON stream; their rollouts in the
private CODEX_HOME are read before it is deleted.
"""
import json
import os
from pathlib import Path
import re
import secrets
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

    def version(self):
        try:
            return subprocess.run(['codex', '--version'], capture_output=True, text=True,
                                  check=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def invocation(self, skill):
        return f'${skill}'

    def prepare(self, trial):
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
            evidence['skills'] += children['skills']
        return evidence

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


def _skill_reads(command):
    return SKILL_FILE.findall(command)


def parse_stream(path):
    """Commands, edits, delegations, usage, and errors from a `codex exec --json` stream."""
    out = new_evidence()
    collab, receivers, spawns = False, set(), 0
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
        elif kind == 'item.completed':
            item = event.get('item') or {}
            if item.get('type') == 'command_execution':
                command = str(item.get('command', ''))
                out['commands'].append(command)
                out['skills'] += _skill_reads(command)
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
    commands, changes, skills, sessions = [], [], [], 0
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
            command = item.get('command')
            if command:
                text = ' '.join(command) if isinstance(command, list) else str(command)
                commands.append(text)
                skills += _skill_reads(text)
            found = item.get('changes')
            if isinstance(found, dict):
                changes += [str(p) for p in found]
            elif isinstance(found, list):
                changes += [str(c.get('path', '')) for c in found if isinstance(c, dict)]
    return {'sessions': sessions, 'commands': commands, 'file_changes': changes, 'skills': skills}


ADAPTER = Codex()
