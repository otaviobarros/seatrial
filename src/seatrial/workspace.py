"""Skill mounts, file manifests, and the run state directory."""
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from seatrial import config

WORKTREE = 'WORKTREE'  # Revision name for the skill files in the current checkout.
NONE = 'NONE'  # Revision name for the baseline arm: no plugin, no skill invocation.
RUNTIME = {'.scratch', '__pycache__'}
IGNORE = ('__pycache__', '*.pyc', '.DS_Store')


def state():
    """The run state directory of the active configuration."""
    return config.current().state


def git(*args, cwd=None, strip=True):
    cwd = cwd or config.current().root
    out = subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout
    return out.strip() if strip else out


def _skipped(path, mounted=True):
    """True for caches, and for runtime artifacts in mounted files; a fixture may seed `.scratch`."""
    return (mounted and bool(RUNTIME.intersection(path.parts))) or any(
        fnmatch.fnmatch(part, pattern) for part in path.parts for pattern in IGNORE)


def changed_files(ref):
    """(Git top level, changed paths, unseen inputs) for changes since ref.

    Compares the merge base of ref and HEAD with the working tree, so `--changed origin/main`
    is what a branch changed. Untracked files count, except caches and files that mounts skip;
    a rename lists both paths. Unseen inputs are identity_files and python_path entries outside
    the repository, whose changes the diff cannot show.
    """
    cfg = config.current()
    try:
        top = Path(git('rev-parse', '--show-toplevel', cwd=cfg.root)).resolve()
        tops = {Path(git('rev-parse', '--show-toplevel', cwd=f)).resolve() for f in (cfg.skills, cfg.cases)
                if f.is_dir()}
    except (subprocess.CalledProcessError, OSError):
        tops = None
    if tops is None or tops - {top}:
        raise ValueError(f'--changed needs {config.FILENAME}, the skills, and the cases in one Git repository')
    try:
        if ref.startswith('-'):
            raise subprocess.CalledProcessError(1, 'git')
        commit = git('rev-parse', '--verify', '--quiet', ref + '^{commit}', cwd=top)
    except subprocess.CalledProcessError:
        raise ValueError(f'--changed {ref}: not a Git revision in {top}') from None
    try:
        base = git('merge-base', commit, 'HEAD', cwd=top)
    except subprocess.CalledProcessError:
        raise ValueError(f'--changed {ref}: no common ancestor with HEAD') from None
    try:
        # Keep -z output whole: a file name may start with a space.
        tracked = git('diff', '--name-only', '-z', '--no-renames', base, '--', cwd=top, strip=False)
        untracked = git('ls-files', '-z', '--others', '--exclude-standard', '--full-name', cwd=top, strip=False)
    except subprocess.CalledProcessError:
        raise ValueError(f'--changed {ref}: cannot list changes in {top}') from None
    paths = (Path(p) for p in (tracked + '\0' + untracked).split('\0') if p)
    cases = cfg.cases.resolve()
    changed = sorted({top / p for p in paths if not _skipped(p, mounted=not (top / p).is_relative_to(cases))})
    unseen = [p for p in [*cfg.identity_files, *cfg.python_path] if not Path(p).resolve().is_relative_to(top)]
    return top, changed, unseen


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


def skill_tree(plugin, skill):
    """Hash what one skill reads in a mounted plugin: its folder and the shared files, not other skills.

    None for the NONE revision, or when the skill is not in the mount. Mounts from a Git
    revision and from the worktree hash alike, so the value tracks content, not origin."""
    if plugin is None or not (Path(plugin) / 'skills' / skill).is_dir():
        return None
    files = {path: digest for path, digest in manifest(plugin).items()
             if Path(path).parts[:1] != ('skills',) or Path(path).parts[1:2] == (skill,)}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def resolve(revision):
    """Identify a revision without mounting it: commit sha and skills tree."""
    if revision == NONE:
        return {'name': NONE, 'sha': None, 'tree': None}
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
    if revision in (WORKTREE, NONE):
        return resolve(revision)
    try:
        source()
    except (subprocess.CalledProcessError, OSError, ValueError):
        raise ValueError(f'revision {revision!r} needs the skills folder in a Git checkout') from None
    try:
        return resolve(revision)
    except subprocess.CalledProcessError:
        hint = f'; use {NONE} for no skill' if revision.upper() == NONE else ''
        raise ValueError(f'revision {revision!r} is not a commit in the skills repository{hint}') from None


def evaluation_inputs(cfg):
    """Paths under the skills folder that belong to the evaluation, relative to it.

    A plugin rooted at the repository root would otherwise mount its cases, graders,
    configuration, and run state next to the skill under evaluation."""
    skills = cfg.skills.resolve()
    found = []
    for path in _input_paths(cfg):
        try:
            rel = Path(path).resolve().relative_to(skills)
        except ValueError:
            continue
        if rel.parts:
            found.append(rel)
    return found


def _input_paths(cfg):
    return [p for p in [cfg.cases, cfg.state, cfg.file, *cfg.identity_files, *cfg.python_path] if p is not None]


def protected_paths(cfg):
    """Real paths of every evaluation input, inside the skills folder or beside it.

    A link in the plugin to any of them is an alias; folders that hold the plugin
    itself, such as a python_path of the repository root, are not protected."""
    skills = Path(os.path.realpath(cfg.skills))
    found = []
    for path in _input_paths(cfg):
        real = Path(os.path.realpath(path))
        if real != skills and real not in skills.parents:
            found.append(real)
    return found


def _overlaps(path, roots):
    """True when path, with links resolved, is, contains, or lies inside one of the real roots."""
    real = Path(os.path.realpath(path))
    return any(real == r or r in real.parents or real in r.parents for r in roots)


def _mount_filter(root, excluded, protected):
    """copytree ignore function: skip caches, Git metadata, evaluation inputs, and links to them."""
    patterns = shutil.ignore_patterns(*IGNORE)
    root, excluded = Path(root), set(excluded)
    real = [Path(os.path.realpath(root / rel)) for rel in excluded] + list(protected)

    def ignore(directory, names):
        skipped = set(patterns(directory, names))
        rel = Path(directory).relative_to(root)
        for name in names:
            entry = Path(directory) / name
            if name == '.git' or rel / name in excluded:
                skipped.add(name)
            elif entry.is_symlink() and _overlaps(entry, real):  # An alias would copy the inputs.
                skipped.add(name)
            elif not entry.is_symlink() and any(Path(os.path.realpath(entry)) == r for r in real):
                skipped.add(name)  # Reached through a followed link.
        return skipped
    return ignore


def _remove_excluded(target, excluded, protected, source):
    """Delete evaluation inputs from an extracted mount without following any link.

    A link on the way to an input is removed, never traversed; a link elsewhere in the
    mount that points at an input, read from the mount or from its place in the source
    tree, is removed too."""
    for rel in excluded:
        path = target
        for part in rel.parts:
            path = path / part
            if path.is_symlink():
                path.unlink()
                break
            if not path.exists():
                break
        else:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    roots = list(protected) + [Path(os.path.realpath(target)) / rel for rel in excluded]
    for directory, dirs, files in os.walk(target):
        for name in dirs + files:
            entry = Path(directory) / name
            if entry.is_symlink() and (_overlaps(entry, roots) or _overlaps(
                    Path(source) / entry.parent.relative_to(target) / os.readlink(entry), roots)):
                entry.unlink()
                if name in dirs:
                    dirs.remove(name)


def mount(revision, dest):
    """Extract the skills folder at one revision, without evaluation inputs; never mix revisions.

    The mount is always plugin-shaped: a `skills` layout is placed under `skills/`
    next to a generated `.claude-plugin/plugin.json`. NONE mounts nothing."""
    cfg = config.current()
    identity = resolve(revision)
    if revision == NONE:
        return identity
    target = dest / 'skills' if cfg.layout == 'skills' else dest
    excluded, protected = evaluation_inputs(cfg), protected_paths(cfg)
    if revision == WORKTREE:
        shutil.copytree(cfg.skills, target, ignore=_mount_filter(cfg.skills, excluded, protected))
    else:
        top, rel = source()
        target.mkdir(parents=True)
        archive = subprocess.run(['git', '-C', str(top), 'archive', identity['sha'], rel],
                                 check=True, capture_output=True).stdout
        depth = 0 if rel == '.' else len(Path(rel).parts)
        subprocess.run(['tar', '-x', '-C', str(target), f'--strip-components={depth}'], input=archive, check=True)
        _remove_excluded(target, excluded, protected, cfg.skills)
    for rel in excluded:  # An emptied parent such as `evals/` still names the evaluation.
        for parent in (target / rel).parents:
            if parent == target or not parent.is_relative_to(target) or parent.is_symlink():
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
