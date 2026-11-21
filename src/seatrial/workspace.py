"""Skill mounts, file manifests, and the run state directory."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from seatrial import config

WORKTREE = 'WORKTREE'  # Revision name for the skill files in the current checkout.
RUNTIME = {'.scratch', '__pycache__'}
IGNORE = ('__pycache__', '*.pyc', '.DS_Store')


def state():
    """The run state directory of the active configuration."""
    return config.current().state


def git(*args, cwd=None):
    cwd = cwd or config.current().root
    return subprocess.run(['git', '-C', str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def source():
    """(Git top level, skills path relative to it) for the active configuration."""
    skills = config.current().skills
    top = Path(git('rev-parse', '--show-toplevel', cwd=skills if skills.is_dir() else skills.parent))
    return top, skills.resolve().relative_to(top.resolve()).as_posix()


def manifest(root, source=False):
    """Hash every file under root. With source=True, skip Git metadata and runtime artifacts."""
    result = {}
    root = Path(root)
    if not root.exists():
        return result
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if source and ('.git' in rel.parts or RUNTIME.intersection(rel.parts) or path.suffix == '.pyc'):
            continue
        if path.is_symlink():
            result[str(rel)] = 'symlink:' + os.readlink(path)
        elif path.is_file():
            result[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def retained(project):
    """Hash what graders read in a retained project, scratch artifacts included.

    The Git index and Python caches are skipped: read-only Git and Python commands
    that graders run can rewrite them without changing the candidate."""
    return {p: h for p, h in manifest(project).items()
            if p != '.git/index' and '__pycache__' not in Path(p).parts and not p.endswith('.pyc')}


def delta(before, after):
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def tree_hash(root):
    return hashlib.sha256(json.dumps(manifest(root), sort_keys=True).encode()).hexdigest()


def resolve(revision):
    """Identify a revision without mounting it: commit sha and skills tree."""
    skills = config.current().skills
    if revision == WORKTREE:
        try:
            top, rel = source()
            sha, dirty = git('rev-parse', 'HEAD', cwd=top), bool(git('status', '--porcelain', '--', rel, cwd=top))
        except (subprocess.CalledProcessError, OSError, ValueError):
            sha, dirty = None, None  # Not a Git checkout: only WORKTREE runs are possible.
        return {'name': WORKTREE, 'sha': sha, 'dirty': dirty, 'tree': 'files:' + tree_hash(skills)}
    top, rel = source()
    full = git('rev-parse', '--verify', revision + '^{commit}', cwd=top)
    return {'name': revision, 'sha': full, 'tree': git('rev-parse', f'{full}:{rel}' if rel != '.' else f'{full}^{{tree}}', cwd=top)}


def check_revision(revision):
    """Resolve one revision or raise ValueError with a message for the user."""
    if revision == WORKTREE:
        return resolve(revision)
    try:
        source()
    except (subprocess.CalledProcessError, OSError, ValueError):
        raise ValueError(f'revision {revision!r} needs the skills folder in a Git checkout') from None
    try:
        return resolve(revision)
    except subprocess.CalledProcessError:
        raise ValueError(f'revision {revision!r} is not a commit in the skills repository') from None


def evaluation_inputs(cfg):
    """Paths under the skills folder that belong to the evaluation, relative to it.

    A plugin rooted at the repository root would otherwise mount its cases, graders,
    configuration, and run state next to the skill under evaluation."""
    skills = cfg.skills.resolve()
    found = []
    for path in [cfg.cases, cfg.state, cfg.file, *cfg.identity_files, *cfg.python_path]:
        if path is None:
            continue
        try:
            rel = Path(path).resolve().relative_to(skills)
        except ValueError:
            continue
        if rel.parts:
            found.append(rel)
    return found


def _mount_filter(root, excluded):
    """copytree ignore function: skip caches, Git metadata, and evaluation inputs."""
    patterns = shutil.ignore_patterns(*IGNORE)
    root, excluded = Path(root), set(excluded)

    def ignore(directory, names):
        skipped = set(patterns(directory, names))
        rel = Path(directory).relative_to(root)
        return skipped | {n for n in names if n == '.git' or rel / n in excluded}
    return ignore


def mount(revision, dest):
    """Extract the skills folder at one revision, without evaluation inputs; never mix revisions.

    The mount is always plugin-shaped: a `skills` layout is placed under `skills/`
    next to a generated `.claude-plugin/plugin.json`."""
    cfg = config.current()
    identity = resolve(revision)
    target = dest / 'skills' if cfg.layout == 'skills' else dest
    excluded = evaluation_inputs(cfg)
    if revision == WORKTREE:
        shutil.copytree(cfg.skills, target, ignore=_mount_filter(cfg.skills, excluded))
    else:
        top, rel = source()
        target.mkdir(parents=True)
        archive = subprocess.run(['git', '-C', str(top), 'archive', identity['sha'], rel],
                                 check=True, capture_output=True).stdout
        depth = 0 if rel == '.' else len(Path(rel).parts)
        subprocess.run(['tar', '-x', '-C', str(target), f'--strip-components={depth}'], input=archive, check=True)
        for rel in excluded:
            path = target / rel
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
    for rel in excluded:  # An emptied parent such as `evals/` still names the evaluation.
        for parent in (target / rel).parents:
            if parent == target or not parent.is_relative_to(target):
                break
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    if cfg.layout == 'skills':
        (dest / '.claude-plugin').mkdir(exist_ok=True)
        (dest / '.claude-plugin/plugin.json').write_text(
            json.dumps({'name': cfg.namespace, 'version': '0.0.0'}, indent=2) + '\n')
    if revision == WORKTREE:
        identity['tree'] = 'files:' + tree_hash(dest)
    return identity


def new_run_directory(stamp=None):
    """A fresh directory for one invocation; concurrent invocations never share one."""
    while True:
        name = stamp or datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        work = state() / name
        try:
            work.mkdir(parents=True)
            return work
        except FileExistsError:
            if stamp:
                raise
            time.sleep(1)
