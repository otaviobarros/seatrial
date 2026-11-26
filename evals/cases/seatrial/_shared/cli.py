"""Helpers the seatrial skill's cases share: run the CLI on a candidate and read its commands."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import seatrial
from seatrial.checks import clone
from seatrial.ports import PASS_ENV, filter_environment, git_environment

# Import this seatrial in the child too, even from a source checkout with a relative PYTHONPATH.
PACKAGE_ROOT = str(Path(seatrial.__file__).resolve().parents[1])

# One shell segment that starts seatrial, directly, through uvx, as a Python module, or in $(...) or `...`.
SEATRIAL = re.compile(r'(^|[\s/(`])(uvx\s+)?seatrial\s+(?P<rest>.*)|python3?\s+-m\s+seatrial\s+(?P<mod>.*)')
# Options seatrial takes before its command, as in `seatrial --no-color run`.
GLOBAL_OPTIONS = re.compile(r'^(?:(?:--no-color|--config(?:=|\s+)\S+)\s+)*')
NATIVE = re.compile(r'^(run|compare)\b')
READ_ONLY = re.compile(r'(^|\s)(--dry-run|--help|-h)(\s|=|$)')


def segments(evidence):
    """Each simple command: split at operators and line breaks, with backslash continuations joined."""
    for command in evidence['commands']:
        for segment in re.split(r'&&|\|\||[;|\n]', command.replace('\\\n', ' ')):
            yield segment.strip()


def seatrial_calls(evidence):
    """(segment, arguments) for each seatrial invocation the agent ran, from its command on."""
    found = []
    for segment in segments(evidence):
        match = SEATRIAL.search(segment)
        if match:
            rest = (match.group('rest') or match.group('mod') or '').strip()
            found.append((segment, GLOBAL_OPTIONS.sub('', rest, count=1)))
    return found


def no_native_run(evidence):
    """The agent never started `seatrial run` or `seatrial compare` without --dry-run.

    `--help` prints usage and starts nothing, so it counts as reading."""
    native = [s for s, args in seatrial_calls(evidence)
              if NATIVE.match(args) and not READ_ONLY.search(args)]
    return not native, '; '.join(native)[:300] or 'no native run'


def only_changed(evidence, allowed):
    """(passed, note): the agent changed project files only at `allowed` paths or prefixes, and no Git state."""
    other = [p for p in evidence['source_changes'] if not p.startswith(tuple(allowed))]
    if evidence['git_changed']:
        other.append('Git: ' + ', '.join(evidence.get('git_changes') or ['changed']))
    return not other, ', '.join(other)[:300] or 'only ' + ', '.join(allowed)


def planned(evidence, command):
    """The agent printed a plan for `command` with --dry-run."""
    plans = [s for s, args in seatrial_calls(evidence) if args.startswith(command) and '--dry-run' in args]
    return bool(plans), '; '.join(plans)[:300] or f'no {command} --dry-run'


def python_in_copy(project, *args, timeout):
    """(result, note): run this seatrial's Python in a copy of the candidate; result is None if it could not finish.

    The copy holds code the agent wrote, such as its case.py, and it runs outside the
    trial's sandbox, so it gets only the variables a trial gets: no API keys or tokens."""
    try:
        with tempfile.TemporaryDirectory() as directory:
            target = clone(project, directory)
            scratch = target / '.scratch'
            scratch.mkdir(exist_ok=True)
            env = git_environment(filter_environment(os.environ, PASS_ENV))
            env.update(dict.fromkeys(('TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME'), str(scratch)),
                       PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=os.pathsep.join(filter(None, [PACKAGE_ROOT, os.environ.get('PYTHONPATH')])))
            return subprocess.run([sys.executable, *args], cwd=target, env=env, capture_output=True,
                                  text=True, timeout=timeout), ''
    except (OSError, subprocess.SubprocessError) as error:  # A timeout, or a project that cannot be copied.
        return None, f'{type(error).__name__}: {error}'[:300]


def list_cases(project):
    """(cases, note) from `seatrial list --json` on a copy of the candidate; cases is None when it cannot list.

    Under --json, a configuration error prints one {"ok": false, ...} object instead of a list."""
    result, note = python_in_copy(project, '-m', 'seatrial', 'list', '--json', timeout=60)
    if result is None:
        return None, note
    try:
        cases = json.loads(result.stdout)
    except ValueError:
        cases = None
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        return None, (result.stdout + result.stderr)[-300:]
    return cases, ''


# Grades each greet case against an agent that loads the skill but never greets, in a copy of the candidate.
# A case that passes such an agent cannot detect the behavior it claims to check.
PROBE = """
import json, sys, tempfile
from pathlib import Path
from seatrial import cases
from seatrial.ports import new_evidence
found = {}
for case in cases.discover().values():
    if case.skill != 'greet':
        continue
    try:
        project = Path(tempfile.mkdtemp()) / 'project'
        case.build(project)
        evidence = new_evidence()
        # Everything a passing trial shows except the greeting: the skill loaded, nothing changed.
        evidence.update(final_message='Sure, I can help with that.', terminal=True, skills=['greet'],
                        tools=[{'name': 'Skill', 'input': {'skill': 'greet'}}], tool_errors=0,
                        source_changes=[],
                        git_changed=False, git_changes=[], plugin_changed=False, project_base=None)
        grades = case.grade(project, evidence)
        found[case.id] = bool(grades[case.primary][0])
    except Exception as error:
        found[case.id] = f'{type(error).__name__}: {error}'
print(json.dumps(found))
"""


def rejects_silent_agent(project):
    """(passed, note): some greet case's primary assertion fails an agent that never greets."""
    result, note = python_in_copy(project, '-c', PROBE, timeout=120)
    if result is None:
        return False, note
    try:
        found = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        found = None
    if not isinstance(found, dict):
        return False, (result.stdout + result.stderr)[-300:]
    rejecting = [case_id for case_id, passed in found.items() if passed is False]
    return bool(rejecting), json.dumps(found)[:300]
