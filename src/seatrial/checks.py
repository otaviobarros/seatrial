"""Shared helpers for case authors: fixture setup and common grading checks.

Every check returns `(passed, note)`, the value a case's grade() maps to an assertion ID.
"""
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tempfile

from seatrial.ports import GIT_IDENTITY, git_environment


def git(project, *args):
    return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                           '-C', str(project), *args], check=True, env=git_environment(os.environ),
                          capture_output=True, text=True).stdout.strip()


def init_repo(project, branch='main'):
    project.mkdir(parents=True)
    git(project, 'init', '--quiet', f'--initial-branch={branch}')
    git(project, 'config', 'user.name', GIT_IDENTITY[0])
    git(project, 'config', 'user.email', GIT_IDENTITY[1])
    # Background maintenance can hold a lock while the project is being removed.
    git(project, 'config', 'maintenance.auto', 'false')
    git(project, 'config', 'gc.auto', '0')


def copy_fixture(source, project, skip=()):
    """Copy a fixture tree. A file named `gitignore` becomes `.gitignore`, so fixture
    ignore rules never apply to this repository."""
    source = Path(source)
    for path in sorted(source.rglob('*')):
        rel = path.relative_to(source)
        if path.is_file() and str(rel) not in skip:
            target = project / ('.gitignore' if str(rel) == 'gitignore' else rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def commit_all(project, message):
    git(project, 'add', '.')
    git(project, 'commit', '--quiet', '-m', message)


def line_of(text, needle):
    """1-based line number of the only line containing needle."""
    hits = [i for i, line in enumerate(text.splitlines(), 1) if needle in line]
    if len(hits) != 1:
        raise ValueError(f'Anchor {needle!r} matched {len(hits)} lines')
    return hits[0]


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


# A test runner invocation, not a file name such as pytest.ini or test_x.py being read.
END = r"""(?=$|[\s"';&|)>])"""
TEST_COMMAND = re.compile(
    r"""(^|[;&|(]|\s|-lc\s+["'])("""
    r"""make(\s+(-[CfIoW]\s*\S+|--(directory|file|makefile|include-dir|assume-old|old-file|assume-new|new-file|what-if)(=\S+|\s+\S+)|-\S+|\w+=\S*))*(\s+check""" + END + r"""|(?=\s*($|["';&|)>])))"""
    r"""|pytest""" + END +
    r"""|python3?(\s+-\S+)*\s+-m\s+(pytest|unittest)"""
    r"""|python3?(\s+-\S+)*\s+\S*test\S*\.py)""")


def ran_tests(evidence):
    """Commands that ran a test runner, from parent and delegated sessions."""
    return [c for c in evidence['commands'] if TEST_COMMAND.search(c)]


def tests_ran(evidence):
    found = ran_tests(evidence)
    return bool(found), '; '.join(found)[:300]


def no_tests_ran(evidence):
    found = ran_tests(evidence)
    return not found, '; '.join(found)[:300]


def probe_runs(evidence, names, suite=False):
    """Command segments that ran Python against the named code. Test-suite runs count
    only when `suite` is true, for example when the agent's own test is red."""
    found = []
    for command in evidence['commands']:
        for segment in re.split(r'&&|\|\||;|\|', command):
            if TEST_COMMAND.search(segment):
                if suite:
                    found.append(segment.strip())
            elif (re.search(r'\bpython3?\b', segment)
                    and not re.search(r'\s--?(version|V)\b', segment)
                    and re.search(names, segment)):
                found.append(segment.strip())
    return found


def preserved(evidence, allow=()):
    """No source, Git, or plugin change outside the allowed source paths."""
    other = [p for p in evidence['source_changes'] if p not in allow]
    ok = not other and not evidence['git_changed'] and not evidence['plugin_changed']
    note = ', '.join(other)[:300]
    if evidence['git_changed']:
        note = ('Git metadata changed; ' + note).strip('; ')
    if evidence['plugin_changed']:
        note = ('plugin changed; ' + note).strip('; ')
    return ok, note


def delegated(evidence):
    """At least one delegated session ran, and its trace was captured."""
    count, traced = evidence['delegations'], evidence['delegation_traces']
    return count > 0 and traced > 0, f'{count} delegations, {traced} traced'


def skill_triggered(evidence, skill):
    """The trace shows the named skill: `plugin:skill`, the bare name, or its SKILL.md path."""
    hits = [resolved for raw, resolved in skill_entries(evidence) if _names(resolved, skill)]
    return bool(hits), '; '.join(hits)[:300] or 'skill not seen in trace'


def skill_entries(evidence):
    """(recorded, resolved) pairs; a relative SKILL.md read resolves against its recorded directory."""
    reads = evidence.get('skill_reads')
    if reads is None:
        return [(s, s) for s in evidence['skills']]
    entries = [(s, s) for s in evidence['skills'] if s not in {r['path'] for r in reads}]
    for read in reads:
        path, cwd = read['path'], read.get('cwd')
        relative = cwd and not path.startswith(('/', '~'))
        entries.append((path, posixpath.normpath(posixpath.join(cwd, path)) if relative else path))
    return entries


def split_skills(evidence):
    """One evidence dict per recorded skill, so an adapter classifies each read on its own."""
    reads = evidence.get('skill_reads')
    if reads is None:
        return [{'skills': [s]} for s in evidence['skills']]
    paths = {r['path'] for r in reads}
    return ([{'skills': [s], 'skill_reads': []} for s in evidence['skills'] if s not in paths]
            + [{'skills': [r['path']], 'skill_reads': [r]} for r in reads])


def skill_loaded(evidence, skill):
    """The host loaded the named skill; the runner flags skills from outside the plugin."""
    return skill_triggered(evidence, skill)


def _names(entry, skill):
    name = posixpath.normpath(entry.replace('\\', '/')) if entry else entry
    return name.lstrip('/') == skill or name.endswith(':' + skill) or name.endswith(f'/{skill}/SKILL.md') \
        or name == f'{skill}/SKILL.md'


def report_matches(evidence, pattern):
    report = evidence['final_message'] or ''
    found = re.search(pattern, report, re.I)
    return bool(found), (found.group(0) if found else report[-200:])


def run(project, *command, timeout=60, env=None):
    """Run a command in a project with scratch-local temporary files."""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', **(env or {}))
    scratch = project / '.scratch'
    scratch.mkdir(exist_ok=True)
    for key in ('TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME'):
        environment[key] = str(scratch)
    return subprocess.run(list(command), cwd=project, env=environment, capture_output=True,
                          text=True, timeout=timeout)


def clone(project, directory):
    """Copy a project for probing without touching the candidate."""
    target = Path(directory) / 'p'
    shutil.copytree(project, target, ignore=shutil.ignore_patterns('.scratch', '__pycache__', '.agents'))
    return target


def line_counts(project, base):
    """Count text-line changes from the fixture commit, including untracked files."""
    paths = ['.', ':(exclude).scratch', ':(exclude).agents', ':(exclude).git']
    env = git_environment(os.environ)

    def call(*args):
        return subprocess.run(['git', '-C', str(project), *args], check=True,
                              capture_output=True, env=env).stdout

    objects = (project / os.fsdecode(call('rev-parse', '--git-path', 'objects')).rstrip('\n')).resolve()
    # Keep the index and new blobs outside the project's Git metadata.
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        (temporary / 'objects/info').mkdir(parents=True)
        (temporary / 'objects/info/alternates').write_text(str(objects) + '\n')
        env.update(GIT_INDEX_FILE=str(temporary / 'index'), GIT_OBJECT_DIRECTORY=str(temporary / 'objects'))
        call('read-tree', base)
        call('add', '-A', '--', '.')
        changes = call('diff', '--cached', '--numstat', '-z', '--find-renames',
                       '--no-ext-diff', '--no-textconv', base, '--', *paths)
    added = removed = 0
    entries = iter(changes.split(b'\0'))
    for entry in entries:
        if entry:
            plus, minus, path = entry.split(b'\t', 2)
            if not path:  # Renames carry old and new paths in separate NUL records.
                next(entries)
                next(entries)
            if plus != b'-':  # Git omits line counts for binary files.
                added += int(plus)
                removed += int(minus)
    return {'lines_added': added, 'lines_removed': removed}
