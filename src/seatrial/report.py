"""Trial grading, per-arm summaries, comparisons, and the Markdown report."""
from statistics import median
import subprocess

from seatrial import checks, stats

# Statuses that are graded. Every other status is invalid and excluded from pass rates.
GRADED = ('completed',)


def grade(record, case, project, evidence):
    """Grade one trial. A grader exception fails every assertion instead of stopping the run."""
    record['grader_sha256'] = case.identity()['grader_sha256']
    record['final_words'] = len((evidence.get('final_message') or '').split())
    for key in ('lines_added', 'lines_removed', 'line_counts_error', 'note', 'assertions', 'pass'):
        record.pop(key, None)
    try:
        record.update(checks.line_counts(project, evidence.get('project_base') or 'HEAD'))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        record['line_counts_error'] = f'{type(error).__name__}: {error}'[:300]
    try:
        graded = case.grade(project, evidence)
    except Exception as error:  # The agent can delete or rewrite any file a grader reads.
        graded = {a: (False, f'grader error: {type(error).__name__}: {error}'[:300]) for a in case.assertions}
    if graded.get('_status') == 'control':
        record['status'] = 'control'
        record['note'] = graded.get('_note')
        return record
    missing = [a for a in case.assertions if a not in graded]
    for assertion in missing:
        graded[assertion] = (False, 'grader returned no result')
    record['assertions'] = [{'id': a, 'passed': bool(graded[a][0]), 'evidence': str(graded[a][1])[:300]}
                            for a in case.assertions]
    record['pass'] = all(a['passed'] for a in record['assertions'])
    return record


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def summarize_arm(case, records):
    valid = [r for r in records if r['status'] in GRADED]
    passes = {a: sum(any(x['id'] == a and x['passed'] for x in r.get('assertions', [])) for r in valid)
              for a in case.assertions}
    statuses = {}
    for r in records:
        statuses[r['status']] = statuses.get(r['status'], 0) + 1
    all_pass = sum(bool(r.get('pass')) for r in valid)
    added = [r['lines_added'] for r in valid if 'lines_added' in r]
    costs = [r['cost_usd'] for r in records if isinstance(r.get('cost_usd'), (int, float))]
    k = min(3, len(valid))
    return {'trials': len(records), 'valid': len(valid), 'statuses': statuses,
            'assertion_passes': passes, 'all_pass': all_pass,
            'pass_at_k': {'k': k, 'value': stats.pass_at_k(len(valid), all_pass, k) if k else None},
            'pass_pow_k': {'k': k, 'value': stats.pass_pow_k(len(valid), all_pass, k) if k else None},
            'lines_added_values': added, 'lines_added_median': median(added) if added else None,
            'wall_s_mean': _mean([r['wall_s'] for r in records if r.get('wall_s') is not None]),
            'commands_mean': _mean([r['commands'] for r in valid if r.get('commands') is not None]),
            'final_words_mean': _mean([r['final_words'] for r in valid if 'final_words' in r]),
            'cost_usd_total': round(sum(costs), 4) if costs else None,
            'isolation_warnings': sum(1 for r in records if r.get('foreign_skills') or r.get('unknown_skills') or r.get('outside_access'))}


def summarize(meta, records, cases):
    """Per case and host: one summary per arm, and a verdict when there are two arms."""
    summary = dict(meta, cases={})
    for case in cases:
        hosts = sorted({r['host'] for r in records if r['case_id'] == case.id})
        entry = {'skill': case.skill, 'suite': case.suite, 'holdout': case.holdout, 'rules': case.rules,
                 'identity': case.identity(), 'primary': case.primary, 'assertions': case.assertions, 'hosts': {}}
        for host in hosts:
            arms = {arm: summarize_arm(case, [r for r in records if r['case_id'] == case.id
                                              and r['host'] == host and r['arm'] == arm])
                    for arm in meta['arms']}
            result = {'arms': arms}
            if set(arms) == {'base', 'head'}:
                result['decision'] = stats.decide(case.assertions, case.primary, arms['base'], arms['head'],
                                                  meta['n'])
                result['lines_added_p_value'] = stats.mann_whitney(arms['base']['lines_added_values'],
                                                                   arms['head']['lines_added_values'])
            entry['hosts'][host] = result
        summary['cases'][case.id] = entry
    costs = [r['cost_usd'] for r in records if isinstance(r.get('cost_usd'), (int, float))]
    summary['cost_usd_total'] = round(sum(costs), 4) if costs else None
    return summary


def markdown(summary):
    lines = ['| Case | Host | Arm | Valid | Primary pass | All pass | pass^k | Mean wall s | Lines added median | Cost USD | Verdict |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            verdict = (result.get('decision') or {}).get('verdict', '-')
            for arm, s in result['arms'].items():
                pow_k = s['pass_pow_k']
                pow_text = f"{pow_k['value']:.2f} (k={pow_k['k']})" if pow_k['value'] is not None else '-'
                primary = s['assertion_passes'][entry['primary']]
                lines.append(f"| {case_id} | {host} | {arm} | {s['valid']}/{s['trials']} | {primary}/{s['valid']} | "
                             f"{s['all_pass']}/{s['valid']} | {pow_text} | {s['wall_s_mean'] or '-'} | "
                             f"{s['lines_added_median'] if s['lines_added_median'] is not None else '-'} | "
                             f"{s['cost_usd_total'] if s['cost_usd_total'] is not None else '-'} | {verdict} |")
    failures = []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            for arm, s in result['arms'].items():
                invalid = {k: v for k, v in s['statuses'].items() if k not in GRADED}
                if invalid:
                    failures.append(f'- {case_id} {host} {arm}: invalid trials {invalid}')
                if s.get('isolation_warnings'):
                    failures.append(f"- {case_id} {host} {arm}: {s['isolation_warnings']} trials used skills "
                                    'from outside the plugin or of unknown source, or named the project path')
    return '\n'.join(lines + ([''] + failures if failures else []))


def terminal(summary):
    """The summary as an aligned, colored table for a terminal."""
    from seatrial import ui
    rows, notes = [], []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            decision = result.get('decision') or {}
            for arm, s in result['arms'].items():
                pow_k = s['pass_pow_k']
                primary = s['assertion_passes'][entry['primary']]
                rows.append([case_id, ui.style(host, 'cyan'), arm, f"{s['valid']}/{s['trials']}",
                             f"{primary}/{s['valid']}", f"{s['all_pass']}/{s['valid']}",
                             f"{pow_k['value']:.2f}" if pow_k['value'] is not None else '-',
                             s['wall_s_mean'] or '-',
                             '-' if s['cost_usd_total'] is None else f"{s['cost_usd_total']:.2f}",
                             ui.verdict(decision['verdict']) if decision and arm == 'head' else ''])
                invalid = {k: v for k, v in s['statuses'].items() if k not in GRADED}
                if invalid:
                    notes.append(ui.warn(f'{case_id} {host} {arm}: invalid trials {invalid}'))
                if s.get('isolation_warnings'):
                    notes.append(ui.warn(f"{case_id} {host} {arm}: {s['isolation_warnings']} trials used skills "
                                         'from outside the plugin or of unknown source, or named the project path'))
            if decision.get('reason'):
                notes.append(ui.info(f"{case_id} {host}: {decision['reason']}"))
    text = ui.table(['Case', 'Host', 'Arm', 'Valid', 'Primary', 'All', 'pass^k', 'Wall s', 'USD', 'Verdict'], rows)
    total = summary.get('cost_usd_total')
    footer = [ui.style(f'total cost USD {total:.2f}', 'dim')] if total is not None else []
    return '\n'.join([text] + notes + footer)


def runs(state, root=None):
    """Retained run directories under state, newest first, each with a short description.

    With root, keep only runs recorded for that project root; the state directory is shared."""
    from pathlib import Path
    import json
    found = []
    for work in sorted(Path(state).glob('*/records.jsonl'), reverse=True):
        work = work.parent
        records = [json.loads(line) for line in (work / 'records.jsonl').read_text().splitlines() if line.strip()]
        meta = {}
        for name in ('summary-regraded.json', 'summary.json', 'run.json'):
            if (work / name).exists():
                meta = json.loads((work / name).read_text())
                break
        if root is not None and meta.get('root') != str(root):
            continue
        costs = [r['cost_usd'] for r in records if isinstance(r.get('cost_usd'), (int, float))]
        found.append({'name': work.name, 'path': str(work), 'project': meta.get('project'),
                      'arms': meta.get('arms'), 'revisions': meta.get('revisions'),
                      'finished': f"{len(records)}/{meta.get('scheduled', '?')}",
                      'hosts': sorted({r['host'] for r in records}),
                      'cases': sorted({r['case_id'] for r in records}),
                      'passed': sum(1 for r in records if r.get('pass')),
                      'cost_usd': round(sum(costs), 4) if costs else None,
                      'summarized': (work / 'summary.json').is_file()})
    return found
