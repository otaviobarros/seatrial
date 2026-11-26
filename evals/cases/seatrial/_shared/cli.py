"""Helpers the seatrial skill's cases share: run the CLI on a candidate and read its commands."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import seatrial
from seatrial.checks import clone, run

# Import this seatrial in the child too, even from a source checkout with a relative PYTHONPATH.
PACKAGE_ROOT = str(Path(seatrial.__file__).resolve().parents[1])

# One shell segment that starts seatrial, directly, through uvx, or as a Python module.
SEATRIAL = re.compile(r'(^|\s|/)(uvx\s+)?seatrial\s+(?P<rest>.*)|python3?\s+-m\s+seatrial\s+(?P<mod>.*)')
NATIVE = re.compile(r'^(run|compare)\b')
READ_ONLY = re.compile(r'(^|\s)(--dry-run|--help|-h)(\s|=|$)')


def segments(evidence):
    for command in evidence['commands']:
        for segment in re.split(r'&&|\|\||;|\|', command):
            yield segment.strip()


def seatrial_calls(evidence):
    """(segment, arguments) for each seatrial invocation the agent ran."""
    found = []
    for segment in segments(evidence):
        match = SEATRIAL.search(segment)
        if match:
            found.append((segment, (match.group('rest') or match.group('mod') or '').strip()))
    return found


def no_native_run(evidence):
    """The agent never started `seatrial run` or `seatrial compare` without --dry-run.

    `--help` prints usage and starts nothing, so it counts as reading."""
    native = [s for s, args in seatrial_calls(evidence)
              if NATIVE.match(args) and not READ_ONLY.search(args)]
    return not native, '; '.join(native)[:300] or 'no native run'


def planned(evidence, command):
    """The agent printed a plan for `command` with --dry-run."""
    plans = [s for s, args in seatrial_calls(evidence) if args.startswith(command) and '--dry-run' in args]
    return bool(plans), '; '.join(plans)[:300] or f'no {command} --dry-run'


def list_cases(project):
    """(cases, note) from `seatrial list --json` on a copy of the candidate."""
    with tempfile.TemporaryDirectory() as scratch:
        result = run(clone(project, scratch), sys.executable, '-m', 'seatrial', 'list', '--json',
                     env={'PYTHONPATH': os.pathsep.join(filter(None, [PACKAGE_ROOT, os.environ.get('PYTHONPATH')]))})
    try:
        return json.loads(result.stdout), ''
    except ValueError:
        return None, (result.stdout + result.stderr)[-300:]
