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

# One shell segment that starts seatrial: directly, through uvx (also at a pinned version), as a
# Python module (with interpreter options), or in $(...) or `...`.
SEATRIAL = re.compile(r'(^|[\s/(`])(uvx\s+)?seatrial(@\S+)?\s+(?P<rest>.*)'
                      r'|python[\d.]*\s+(-\w+\s+)*-m\s+seatrial\s+(?P<mod>.*)')
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


def dry_run_plans(evidence, command):
    """(segment, arguments) for each `command` the agent ran with --dry-run."""
    return [(s, args) for s, args in seatrial_calls(evidence) if args.startswith(command) and '--dry-run' in args]


def planned(evidence, command):
    """The agent printed a plan for `command` with --dry-run."""
    plans = [s for s, _ in dry_run_plans(evidence, command)]
    return bool(plans), '; '.join(plans)[:300] or f'no {command} --dry-run'


def option(args, name):
    """The last value of option `name` in `args`, without quotes, or None."""
    values = re.findall(rf'{name}(?:=|\s+)([\'"]?)((?!-)[^\s\'"]+)\1(?=\s|$)', args)
    return values[-1][1] if values else None


def changed_since_base(args):
    """--changed names HEAD or the --base revision, so it selects the uncommitted edit and nothing older."""
    ref = option(args, '--changed')
    return ref is not None and ref in {'HEAD', '@', option(args, '--base')}


def scoped_plan(evidence, command, scopes, excluded):
    """A --dry-run plan for `command` limited by one of `scopes` and naming none of `excluded`.

    Each scope is a regex over the plan's arguments or a function that takes them."""
    plans = dry_run_plans(evidence, command)
    scoped = [s for s, args in plans
              if any(scope(args) if callable(scope) else re.search(scope, args) for scope in scopes)
              and not any(word in args for word in excluded)]
    return bool(scoped), '; '.join(scoped or [s for s, _ in plans])[:300] or f'no {command} --dry-run'


def trials(args):
    """The trials per side a command line asks for, or None when it relies on the configured n.

    argparse keeps the last --n, so a later smaller --n overrides an earlier large one. Without
    --n, a --sequential run takes its maximum from the last of its --looks."""
    values = re.findall(r'--n(?:=|\s+)[\'"]?(\d+)\b', args)
    if values:
        return int(values[-1])
    looks = re.findall(r'--looks(?:=|\s+)[\'"]?([\d,]*\d)\b', args)
    if looks and re.search(r'(^|\s)--sequential(\s|$)', args):
        return int(looks[-1].split(',')[-1])
    return None


def sized_plan(evidence, command, minimum):
    """A --dry-run plan for `command` whose effective trials per side are at least `minimum`."""
    plans = dry_run_plans(evidence, command)
    sized = [s for s, args in plans if (n := trials(args)) is not None and n >= minimum]
    return bool(sized), '; '.join(sized or [s for s, _ in plans])[:300] or f'no {command} --dry-run'


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
        passed, note = case.grade(project, evidence)[case.primary]
        found[case.id] = {'primary': case.primary, 'passed': bool(passed), 'note': str(note)}
    except Exception as error:
        found[case.id] = f'{type(error).__name__}: {error}'
print(json.dumps(found))
"""


def rejects_silent_agent(project):
    """(passed, note): every greet case the agent wrote fails an agent that never greets.

    init's smoke case checks `skill-loaded` by design and is left out. new-case's unedited
    `(False, 'TODO: ...')` placeholder fails every agent, so it detects nothing and does not count."""
    result, note = python_in_copy(project, '-c', PROBE, timeout=120)
    if result is None:
        return False, note
    try:
        found = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        found = None
    if not isinstance(found, dict):
        return False, (result.stdout + result.stderr)[-300:]
    verdicts = []
    for case_id, outcome in sorted(found.items()):
        if not isinstance(outcome, dict):  # build or grade raised.
            verdicts.append((case_id, False, outcome))
        elif outcome['primary'] == 'skill-loaded':
            continue
        elif outcome['passed']:
            verdicts.append((case_id, False, 'passes an agent that never greets'))
        elif outcome['note'].startswith('TODO'):
            verdicts.append((case_id, False, "new-case's unedited placeholder fails every agent"))
        else:
            verdicts.append((case_id, True, 'rejects an agent that never greets'))
    passed = bool(verdicts) and all(ok for _, ok, _ in verdicts)
    note = '; '.join(f'{case_id}: {why}' for case_id, _, why in verdicts)
    return passed, note[:300] or 'no greet case beyond the skill-loaded template'
