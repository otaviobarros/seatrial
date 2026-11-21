"""Project setup (`init`) and environment checks (`doctor`)."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from seatrial import config, ui
from seatrial import cases as case_index
from seatrial import hosts as registry
from seatrial.templates import CASE_PY, CASE_TOML, EXAMPLE_PY, FIXTURE_README, toml_string

SKIP = {'node_modules', 'venv', '__pycache__', 'dist', 'build', 'worktrees'}
# Hidden folders that hold a project's own agent skills. Other dot folders hold copies:
# worktrees, scratch space, and release staging.
DOT_FOLDERS = {'.claude', '.agents', '.codex', '.github'}


def linked_worktree(folder):
    """A linked worktree's .git file points into <repo>/.git/worktrees; a submodule's into modules."""
    marker = folder / '.git'
    try:
        return marker.is_file() and '/worktrees/' in marker.read_text(errors='replace')
    except OSError:
        return False


def find_skills(root, depth=5):
    """Candidate skill folders under root: (path, layout, namespace, skill names)."""
    root = Path(root).resolve()
    found = {}
    for current, directories, files in os.walk(root):
        rel = Path(current).relative_to(root)
        directories[:] = [d for d in directories if d not in SKIP and (not d.startswith('.') or d in DOT_FOLDERS)
                          and not linked_worktree(Path(current) / d)]
        if len(rel.parts) >= depth:
            directories[:] = []
        if 'SKILL.md' in files:
            skill = Path(current)
            folder = skill.parent
            plugin = folder.parent if folder.name == 'skills' else None
            if plugin and (plugin / '.claude-plugin/plugin.json').is_file():
                key, layout = plugin, 'plugin'
            else:
                key, layout = folder, 'skills'
            entry = found.setdefault(key, {'path': key, 'layout': layout, 'skills': []})
            entry['skills'].append(skill.name)
            directories[:] = []
    result = []
    for entry in found.values():
        namespace = config.plugin_name(entry['path'], entry['layout']) or entry['path'].name
        if entry['layout'] == 'skills' and entry['path'].name == 'skills':
            namespace = entry['path'].parent.name  # A folder named skills says nothing; use its project.
        result.append({**entry, 'namespace': namespace, 'skills': sorted(entry['skills'])})
    return sorted(result, key=lambda e: (-len(e['skills']), str(e['path'])))


def skill_names(cfg):
    base = cfg.skills / 'skills' if cfg.layout == 'plugin' else cfg.skills
    return sorted(p.parent.name for p in base.glob('*/SKILL.md'))


def relative(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix() or '.'
    except ValueError:
        return str(path)


def init(root, skills=None, layout=None, namespace=None, cases='evals/cases', hosts=None,
         name=None, example=True, force=False, prompt=False):
    """Write seatrial.toml and an example case. Return a dict describing what was written."""
    root = Path(root).resolve()
    target = root / config.FILENAME
    if target.exists() and not force:
        raise config.ConfigError(f'{target} exists; pass --force to replace it')
    candidates = find_skills(root)
    if skills is None:
        if not candidates:
            raise config.ConfigError(f'no SKILL.md found under {root}; pass --skills PATH')
        chosen = candidates[0]
        if prompt and len(candidates) > 1:
            chosen = ui.choose('Which folder holds the skills to evaluate?',
                               [(c, f"{relative(c['path'], root)}  {ui.style(c['layout'], 'cyan')}  "
                                    f"{ui.style(str(len(c['skills'])) + ' skills', 'dim')}") for c in candidates])
        skills, layout = relative(chosen['path'], root), layout or chosen['layout']
        namespace = namespace or chosen['namespace']
    path = (root / skills).resolve()
    if not path.is_dir():
        raise config.ConfigError(f'skills folder {path} does not exist')
    if layout is None:
        layout = 'plugin' if (path / 'skills').is_dir() else 'skills'
    namespace = namespace or config.plugin_name(path, layout) or path.name
    available = sorted(registry.available())
    hosts = hosts or [h for h in ('codex', 'claude') if h in available]
    if prompt:
        namespace = ui.ask('Plugin namespace for Claude (/<namespace>:<skill>)', namespace)
        cases = ui.ask('Folder for eval cases', cases)
        detected = [h for h in available if shutil.which(h)]
        hosts = ui.choose_many('Which agent CLIs (hosts) should run the cases?',
                               [(h, h + (ui.style('  installed', 'green') if h in detected else
                                         ui.style('  not found on PATH', 'dim'))) for h in available],
                               preselected=detected or hosts)
        example = ui.confirm('Create a runnable example case?', example)
    unknown = [h for h in hosts if h not in available]
    if unknown:
        raise config.ConfigError(f'unknown hosts {unknown}; registered: {available}')
    target.write_text(config.render(name or root.name, skills, layout, namespace, cases, hosts))
    written = [str(target)]
    cfg = config.activate(config.load(target))
    cfg.cases.mkdir(parents=True, exist_ok=True)
    names = skill_names(cfg)
    example_id = f'{names[0]}-smoke-001' if names else None
    if (example and names and case_index.ID.match(example_id)
            and example_id not in case_index.discover(cfg.cases)):
        written += new_case(cfg, names[0], example_id, example=True)
    return {'config': str(target), 'skills': str(path), 'layout': layout, 'namespace': namespace,
            'skills_found': names, 'cases': str(cfg.cases), 'hosts': hosts, 'written': written}


def new_case(cfg, skill, case_id, summary=None, example=False):
    """Create a case folder; return the written paths."""
    if not case_index.ID.match(case_id):
        raise ValueError('a case id looks like name-001')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', skill) or skill.startswith('..'):
        raise ValueError('a skill name uses letters, digits, hyphens, underscores, and dots')
    if case_id in case_index.discover(cfg.cases):
        raise ValueError(f'case {case_id} already exists')
    primary = 'skill-loaded' if example else 'behavior'
    summary = summary or ('The host loads the skill and leaves the project unchanged.' if example
                          else 'TODO: one sentence on the behavior this case checks.')
    text = CASE_TOML.format(id=toml_string(case_id), skill=toml_string(skill),
                            summary=toml_string(summary), primary=primary)
    tomllib.loads(text)
    directory = cfg.cases / skill / case_id
    (directory / 'fixture').mkdir(parents=True)
    (directory / 'case.toml').write_text(text)
    (directory / 'case.py').write_text(EXAMPLE_PY if example else CASE_PY)
    (directory / 'fixture/README.md').write_text(FIXTURE_README)
    return [str(directory / name) for name in ('case.toml', 'case.py', 'fixture/README.md')]


def doctor(cfg=None, probe_hosts=True, path=None):
    """Run environment checks. Each result is {check, level, detail}; level is ok, warn, or fail.

    Without cfg, load path, else the configuration found from the working directory."""
    results = []

    def add(check, level, detail):
        results.append({'check': check, 'level': level, 'detail': detail})

    add('python', 'ok' if sys.version_info >= (3, 11) else 'fail', sys.version.split()[0])
    git = shutil.which('git')
    add('git', 'ok' if git else 'fail', git or 'git is not on PATH')
    if cfg is None:
        path = path or config.find()
        if path is None:
            add('config', 'fail', f'no {config.FILENAME} here or above; run `seatrial init`')
            return results
        try:
            cfg = config.activate(config.load(path))
        except config.ConfigError as error:
            add('config', 'fail', str(error))
            return results
    add('config', 'ok', str(cfg.file or cfg.root))
    names = skill_names(cfg) if cfg.skills.is_dir() else []
    add('skills', 'ok' if names else 'fail',
        f'{ui.count(len(names), "skill")} in {relative(cfg.skills, cfg.root)} ({cfg.layout}, /{cfg.namespace}:<skill>)'
        if names else f'no <name>/SKILL.md under {cfg.skills}')
    try:
        found = case_index.discover()
        unknown = sorted({c.skill for c in found.values()} - set(names))
        add('cases', 'ok' if found else 'warn',
            f'{ui.count(len(found), "case")} in {relative(cfg.cases, cfg.root)}' if found else
            'no cases yet; run `seatrial new-case`')
        if unknown:
            add('case skills', 'warn', 'cases name skills that are not in the skills folder: ' + ', '.join(unknown))
    except (ValueError, OSError) as error:
        add('cases', 'fail', str(error))
    try:
        top = subprocess.run(['git', '-C', str(cfg.skills), 'rev-parse', '--show-toplevel'], check=True,
                             capture_output=True, text=True).stdout.strip()
        add('revisions', 'ok', f'Git checkout {top}; compare can mount revisions')
    except (subprocess.CalledProcessError, OSError):
        add('revisions', 'warn', 'skills are not in a Git checkout; only WORKTREE runs work')
    try:
        cfg.state.mkdir(parents=True, exist_ok=True)
        add('state', 'ok' if os.access(cfg.state, os.W_OK) else 'fail', str(cfg.state))
    except OSError as error:
        add('state', 'fail', f'{cfg.state}: {error}')
    try:
        adapters = registry.available()
    except (ImportError, ValueError) as error:
        add('hosts', 'fail', str(error))
        return results
    for host in cfg.hosts:
        if host not in adapters:
            add(f'host {host}', 'fail', f'no adapter named {host}; registered: {", ".join(sorted(adapters))}')
            continue
        version = adapters[host].version() if probe_hosts else 'not probed'
        add(f'host {host}', 'ok' if version else 'warn',
            f'{version}; model {registry.model(host)}' if version else f'{host} CLI not found or not working')
    user_skills = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'skills'
    if 'claude' in cfg.hosts and not os.environ.get('ANTHROPIC_API_KEY') and any(user_skills.glob('*/SKILL.md')):
        add('claude isolation', 'warn', f'skills in {user_skills} may load in Claude trials; set '
            'ANTHROPIC_API_KEY to give each trial its own config, or check foreign_skills in the records')
    return results


def doctor_text(results):
    marks = {'ok': ui.ok, 'warn': ui.warn, 'fail': ui.bad}
    return '\n'.join(marks[r['level']](f"{ui.style(r['check'], 'bold')}  {r['detail']}") for r in results)


def doctor_json(results):
    return json.dumps({'ok': not any(r['level'] == 'fail' for r in results), 'checks': results}, indent=2)
