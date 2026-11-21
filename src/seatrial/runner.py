"""Run trials: mount skill revisions, isolate each host, launch, collect, and grade.

Records go to `<run>/records.jsonl` as each trial ends, so an interrupted run keeps
its finished trials. Each trial directory keeps its project and evidence for regrading;
private host state, including any copied login, is removed when the trial ends.
"""
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

from seatrial import config, report
from seatrial.ports import Trial, validate_evidence
from seatrial.process import launch
from seatrial.workspace import check_revision, delta, manifest, mount, new_run_directory, retained

STOP = ('usage_limit', 'auth_error')


def schedule(cases, hosts, arms, n):
    """Interleave arms so time and load effects reach both arms evenly."""
    jobs = []
    for index in range(n):
        for position, case in enumerate(cases):
            for host in hosts:
                if host not in case.hosts:
                    continue
                order = arms if (index + position) % 2 == 0 else list(reversed(arms))
                jobs += [{'case': case, 'host': host, 'arm': arm, 'index': index} for arm in order]
    return jobs


def trial_timeout(case, context):
    """The override, else the case value, else the default; never above the cap."""
    return min(context['timeout_override'] or case.timeout or context['timeout'], limits()['max_timeout'])


def defaults():
    """Run defaults from the active configuration."""
    active = config.active()
    return active.defaults if active else dict(config.DEFAULTS)


def limits():
    """Hard caps from the active configuration."""
    active = config.active()
    return active.limits if active else dict(config.LIMITS)


def _project_base(project):
    if not (project / '.git').exists():
        return None
    try:
        return subprocess.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def _source_manifest(project, installed):
    found = manifest(project, source=True)
    try:
        inside = installed.resolve().relative_to(project.resolve())
    except ValueError:
        return found
    prefix = str(inside) + os.sep
    return {p: h for p, h in found.items() if not p.startswith(prefix)}


def isolation_findings(host, trial, evidence):
    """Skills from outside the plugin, and commands that name the evaluated project.

    Trials run outside the project, so a command with its path means the agent found
    the cases or graders; a grade from such a trial is not a clean measurement."""
    found = {}
    foreign = host.foreign_skills(trial, evidence)
    if foreign:
        found['foreign_skills'] = sorted(set(foreign))[:10]
    unknown = host.unknown_skills(trial, evidence)
    if unknown:
        found['unknown_skills'] = sorted(set(unknown))[:10]
    root = config.current().root
    if root not in Path(trial.directory).resolve().parents:
        path = re.compile(re.escape(str(root)) + r'(?![\w.-])')
        named = [c for c in evidence['commands'] if path.search(c)]
        if named:
            found['outside_access'] = [c[:300] for c in named[:5]]
    return found


def run_trial(job, context):
    case, host, arm = job['case'], context['adapters'][job['host']], job['arm']
    directory = context['work'] / 'trials' / secrets.token_hex(4)  # Neutral, agent-visible path.
    project = directory / case.project
    directory.mkdir(parents=True)
    effort = context['effort'] or case.effort
    timeout = trial_timeout(case, context)
    prompt = host.invocation(case.skill) + '\n' + case.build(project)
    trial = Trial(directory, project, context['mounts'][arm], prompt, context['models'][host.name],
                  effort, timeout, context['budget_usd'])
    record = {'case_id': case.id, 'case_version': case.version, 'host': host.name, 'arm': arm,
              'trial_index': job['index'], 'revision': context['revisions'][arm],
              'cli_version': context['versions'][host.name], 'model': trial.model, 'effort': effort,
              'flags_sha256': host.pin(trial.model, effort), 'trial_dir': str(directory)}
    installed = None
    try:
        installed = host.prepare(trial)
        before = (_source_manifest(project, installed), manifest(project / '.git'), manifest(installed))
        project_base = _project_base(project)
        started = datetime.datetime.now(datetime.timezone.utc)
        process = launch(host.command(trial), project, host.environment(trial, os.environ),
                         trial.trace, trial.stderr, trial.timeout)
        evidence = host.collect(trial)
    finally:
        record.update(host.release(trial, context['lock']))
    problems = validate_evidence(evidence)
    if problems:
        raise ValueError(f'{host.name} adapter returned invalid evidence: {problems}')
    evidence.update(project_base=project_base,
                    source_changes=delta(before[0], _source_manifest(project, installed)),
                    git_changed=bool(delta(before[1], manifest(project / '.git'))),
                    plugin_changed=bool(delta(before[2], manifest(installed))))
    (directory / 'evidence.json').write_text(json.dumps(evidence) + '\n')
    stderr = trial.stderr.read_text(errors='replace')[-4000:] if trial.stderr.exists() else ''
    status = host.classify(process, evidence, stderr)
    record.update(started_at=started.isoformat(timespec='seconds'),
                  wall_s=round(process['ended'] - process['started'], 1), exit=process.get('exit'),
                  stalled_s=process.get('stalled_s', 0.0), status=status, usage=evidence['usage'],
                  cost_usd=evidence['cost_usd'], commands=len(evidence['commands']),
                  delegations=evidence['delegations'], delegation_traces=evidence['delegation_traces'],
                  skills=sorted(set(evidence['skills']))[:20])
    record.update(isolation_findings(host, trial, evidence))
    if process.get('kill_failed'):  # Leaked processes must be visible, never silent.
        record['kill_failed'] = process['kill_failed']
    if status == 'completed':
        report.grade(record, case, project, evidence)
        # Regrading must measure this candidate, not a later edit of the retained files.
        (directory / 'retained.json').write_text(json.dumps(
            {'project': retained(project), 'evidence_sha256': file_sha256(directory / 'evidence.json')}) + '\n')
    else:
        record['errors'] = evidence['errors'][-2:] + ([stderr[-500:]] if stderr.strip() else [])
    return record


def execute(cases, hosts, revisions, options, on_record=None):
    """Run every scheduled trial and return (meta, records, run directory).

    revisions maps arm names to revision names, for example {'base': 'main', 'head': 'WORKTREE'}.
    on_record(record, finished, scheduled) is called once per finished trial."""
    from seatrial import hosts as registry
    adapters = {name: registry.get(name) for name in hosts}
    versions = {name: adapter.version() for name, adapter in adapters.items()}
    missing = [name for name, version in versions.items() if version is None]
    if missing:
        raise SystemExit(f'host CLI unavailable: {", ".join(missing)}')
    arms = list(revisions)
    jobs = schedule(cases, hosts, arms, options['n'])
    for revision in revisions.values():
        try:
            check_revision(revision)
        except ValueError as error:
            raise SystemExit(str(error)) from None
    work = new_run_directory()
    mounts, identities = {}, {}
    for position, (arm, revision) in enumerate(revisions.items()):
        mounts[arm] = work / f'p{position}'
        identities[arm] = mount(revision, mounts[arm])
    context = {'work': work, 'adapters': adapters, 'versions': versions, 'mounts': mounts,
               'revisions': identities, 'models': options['models'], 'effort': options['effort'],
               'timeout': options['timeout'] or defaults()['timeout'], 'timeout_override': options['timeout'],
               'budget_usd': options['budget_usd'], 'lock': threading.Lock()}
    cfg = config.current()
    started = {'created_at': work.name, 'project': context_name(), 'root': str(cfg.root), 'arms': arms,
               'revisions': identities, 'hosts': hosts, 'cli_versions': versions,
               'models': {h: options['models'][h] for h in hosts}, 'effort_override': options['effort'],
               'n': options['n'], 'jobs': options['jobs'], 'max_minutes': options['max_minutes'],
               'max_cost_usd': options['max_cost_usd'], 'budget_usd': options['budget_usd'],
               'scheduled': len(jobs)}
    (work / 'run.json').write_text(json.dumps(started, indent=2) + '\n')
    stop, records, lock = threading.Event(), [], threading.Lock()
    deadline = time.monotonic() + options['max_minutes'] * 60
    out = work / 'records.jsonl'
    spent = [0.0]

    def worker(job):
        if stop.is_set() or time.monotonic() + trial_timeout(job['case'], context) > deadline:
            return
        if options['max_cost_usd'] is not None and spent[0] >= options['max_cost_usd']:
            stop.set()
            return
        try:
            record = run_trial(job, context)
        except Exception as error:  # Keep the run and its summary; report the failed trial.
            record = {'case_id': job['case'].id, 'case_version': job['case'].version, 'host': job['host'],
                      'arm': job['arm'], 'trial_index': job['index'], 'status': 'harness_error',
                      'wall_s': None, 'errors': [f'{type(error).__name__}: {error}'[:500]]}
        except BaseException:  # Ctrl-C or exit: no queued trial may start.
            stop.set()
            raise
        if record['status'] in STOP:
            stop.set()
        with lock:
            if isinstance(record.get('cost_usd'), (int, float)):
                spent[0] += record['cost_usd']
            records.append(record)
            with out.open('a') as handle:
                handle.write(json.dumps(record) + '\n')
            if on_record:
                on_record(record, len(records), len(jobs))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=options['jobs'])
    try:
        futures = [pool.submit(worker, job) for job in jobs]  # Ctrl-C here is cancelled like one below.
        for future in futures:
            future.result()
    except BaseException:
        # Stop before joining: queued trials are cancelled, a running one finishes and keeps its record.
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    meta = {**started, 'finished': len(records), 'stopped_early': stop.is_set() or len(records) < len(jobs)}
    return meta, records, work


def run_meta(work):
    """The run's metadata: the final summary when the run finished, else what it recorded at start."""
    for name in ('summary.json', 'run.json'):
        if (Path(work) / name).is_file():
            return json.loads((Path(work) / name).read_text())
    return {}


def context_name():
    active = config.active()
    return active.name if active else None


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def retained_changes(directory, project):
    """What changed in a retained trial since grading: project files and the saved evidence."""
    saved = directory / 'retained.json'
    if not saved.exists():
        return ['retained.json is missing']
    data = json.loads(saved.read_text())
    if 'project' not in data:
        return ['retained.json predates evidence digests']
    changed = delta(data['project'], retained(directory / project))
    if data.get('evidence_sha256') != file_sha256(directory / 'evidence.json'):
        changed.append('evidence.json')
    return changed


def regrade(work, cases):
    """Grade retained trials again with the current case code; both arms alike."""
    work = Path(work)
    meta = run_meta(work)
    if not meta:
        raise ValueError(f'{work} has neither summary.json nor run.json; it cannot be regraded')
    meta = {k: v for k, v in meta.items() if k not in ('cases', 'cost_usd_total')}
    records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines() if line]
    meta.setdefault('finished', len(records))
    meta.setdefault('stopped_early', len(records) < meta.get('scheduled', len(records)))
    for record in records:
        if record['status'] not in ('completed', 'control') or record['case_id'] not in cases:
            continue
        case = cases[record['case_id']]
        directory = Path(record['trial_dir'])
        changed = retained_changes(directory, case.project)
        if changed:  # Invalid, never regraded: the files are no longer the trial's candidate.
            record.update(status='stale', errors=['retained files changed: ' + ', '.join(changed)[:400]])
            for key in ('assertions', 'pass'):
                record.pop(key, None)
            continue
        evidence = json.loads((directory / 'evidence.json').read_text())
        record['status'] = 'completed'
        record['regraded_from_version'] = record.get('regraded_from_version', record['case_version'])
        record['case_version'] = case.version
        report.grade(record, case, directory / case.project, evidence)
    (work / 'records-regraded.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    meta['regraded'] = True
    return meta, records
