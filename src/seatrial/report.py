"""Trial grading, per-arm summaries, comparisons, and the Markdown report."""
from statistics import median
import subprocess

from seatrial import checks, stats
from seatrial.workspace import NONE

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
        graded = {a: (False, f'grader error: {type(error).__name__}: {error}'[:300])
                  for a in case.assertions if a != 'skill_triggered'}  # The trace still grades it.
    if 'skill_triggered' in case.assertions and 'skill_triggered' not in graded:
        graded['skill_triggered'] = checks.skill_triggered(evidence, case.skill)
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


def _interval(c, n):
    return stats.rate(c, n)['ci']


def primary_text(s, primary):
    """The primary pass count with its 95% Wilson interval, for example `7/10 (40–89%)`."""
    # primary_ci is missing in summaries written before intervals were added.
    return stats.percent({'passes': s['assertion_passes'][primary], 'valid': s['valid'], 'ci': s.get('primary_ci')})


EVIDENCE_TEXT = {'zero_tolerance': 'zero-tolerance rule', 'strong': 'strong evidence, p < {strong:.2g}',
                 'significant': 'significant, p < {alpha:.2g}', 'not_significant': 'not significant',
                 'insufficient_trials': 'too few valid trials'}


def gate_alpha(summary):
    """The threshold the verdicts used: 0.05, or a sequential run's per-look threshold."""
    return (summary.get('multiple_comparisons') or {}).get('alpha', stats.ALPHA)


def reading(result, alpha=stats.ALPHA):
    """The interpretation as one line: verdict, evidence, explanation, and recommendation."""
    meaning = result.get('interpretation')
    if not meaning:
        return None
    evidence = EVIDENCE_TEXT[meaning['evidence']].format(strong=alpha / 5, alpha=alpha)
    if (result.get('decision') or {}).get('adjusted_p_values'):
        evidence = evidence.replace('p <', 'adjusted p <')
    parts = [f"{meaning['verdict']} ({evidence}).", meaning['explanation'],
             meaning['recommendation']]
    return ' '.join(p for p in parts if p)


def verdict_text(result):
    """The verdict, with * when the multiple-comparison correction changed it, where a sequential
    run stopped, and a note when the run had too few trials to detect the observed change."""
    decision = result.get('decision')
    if not decision:
        return '-'
    text = decision['verdict'] + ('*' if 'verdict_uncorrected' in decision else '')
    stopped = stop_text(decision)
    text += f' ({stopped})' if stopped else ''
    return f'{text}, underpowered' if (result.get('interpretation') or {}).get('underpowered') else text


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
    ci = {a: _interval(passes[a], len(valid)) for a in case.assertions}
    return {'trials': len(records), 'valid': len(valid), 'statuses': statuses,
            'assertion_passes': passes, 'assertion_ci': ci, 'primary_ci': ci[case.primary], 'all_pass': all_pass,
            'pass_at_k': {'k': k, 'value': stats.pass_at_k(len(valid), all_pass, k) if k else None},
            'pass_pow_k': {'k': k, 'value': stats.pass_pow_k(len(valid), all_pass, k) if k else None},
            'lines_added_values': added, 'lines_added_median': median(added) if added else None,
            'wall_s_mean': _mean([r['wall_s'] for r in records if r.get('wall_s') is not None]),
            'commands_mean': _mean([r['commands'] for r in valid if r.get('commands') is not None]),
            'final_words_mean': _mean([r['final_words'] for r in valid if 'final_words' in r]),
            'cost_usd_total': round(sum(costs), 4) if costs else None,
            'isolation_warnings': sum(1 for r in records if r.get('foreign_skills') or r.get('unknown_skills') or r.get('outside_access'))}


def summarize(meta, records, cases, correction='none'):
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
                result['lines_added_p_value'] = stats.mann_whitney(arms['base']['lines_added_values'],
                                                                   arms['head']['lines_added_values'])
            entry['hosts'][host] = result
        summary['cases'][case.id] = entry
    costs = [r['cost_usd'] for r in records if isinstance(r.get('cost_usd'), (int, float))]
    summary['cost_usd_total'] = round(sum(costs), 4) if costs else None
    return correct(summary, correction)


def correct(summary, method):
    """Recompute every verdict with p-values adjusted across the run's Fisher tests.

    The family is every assertion of every case and host with a decision whose margins
    allow p < alpha; an assertion that cannot reach significance, such as 10/10 against
    10/10, stays outside it and keeps its raw p-value. A new zero-tolerance failure does
    not use p-values. A sequential run tests at its per-look threshold; the
    non-inferiority margin keeps the planned n, so an early stop never widens it. The
    interpretation is rebuilt from the final verdict. Works on a stored summary too."""
    sequential = summary.get('sequential') or {}
    looks = summary.get('looks') or sequential.get('looks')
    alpha = stats.look_threshold(looks) if looks else stats.ALPHA
    plain, tests = {}, []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            arms = result['arms']
            if set(arms) != {'base', 'head'}:
                continue
            record = (sequential.get('cases') or {}).get(case_id, {}).get(host)
            n = (record or {}).get('stopped_at') or summary['n']
            decision = stats.decide(entry['assertions'], entry['primary'], arms['base'], arms['head'], n,
                                    alpha=alpha, margin_n=summary['n'])
            plain[(case_id, host)] = (decision, n, record)
            for assertion, p in sorted((decision.get('p_values') or {}).items()):
                counts = (arms['base']['assertion_passes'][assertion], arms['base']['valid'],
                          arms['head']['assertion_passes'][assertion], arms['head']['valid'])
                if stats.min_attainable_p(*counts) < alpha:
                    tests.append((case_id, host, assertion, p))
    adjusted = {key: dict(decision['p_values']) for key, (decision, _, _) in plain.items()
                if 'p_values' in decision}
    for (case_id, host, assertion, _), value in zip(tests, stats.adjust([t[3] for t in tests], method)):
        adjusted[(case_id, host)][assertion] = value
    for (case_id, host), (decision, n, record) in plain.items():
        if method != 'none' and (case_id, host) in adjusted:
            entry = summary['cases'][case_id]
            arms = entry['hosts'][host]['arms']
            corrected = stats.decide(entry['assertions'], entry['primary'], arms['base'], arms['head'], n,
                                     adjusted=adjusted[(case_id, host)], alpha=alpha, margin_n=summary['n'])
            if corrected['verdict'] != decision['verdict']:
                corrected['verdict_uncorrected'] = decision['verdict']
            decision = corrected
        if record:
            decision['sequential'] = dict(record, superseded=stop_superseded(record, decision))
        summary['cases'][case_id]['hosts'][host]['decision'] = decision
    # Trial counts use a Bonferroni bound on the per-test threshold: conservative for holm and bh.
    test_alpha = alpha / len(tests) if method != 'none' and tests else alpha
    for case_id, host in plain:
        entry = summary['cases'][case_id]
        result = entry['hosts'][host]
        result['interpretation'] = stats.interpret(entry['assertions'], entry['primary'], result['arms']['base'],
                                                   result['arms']['head'], result['decision'],
                                                   alpha=alpha, test_alpha=test_alpha)
    summary['multiple_comparisons'] = {'method': method, 'family_size': len(tests), 'alpha': alpha}
    return summary


def correction_note(summary):
    """One line naming the correction, or None when verdicts use raw p-values."""
    info = summary.get('multiple_comparisons') or {}
    if info.get('method', 'none') == 'none':
        return None
    changed = sum(1 for entry in summary['cases'].values() for result in entry['hosts'].values()
                  if 'verdict_uncorrected' in (result.get('decision') or {}))
    return (f"verdicts corrected with {info['method']} across {info['family_size']} Fisher tests; "
            f"{changed} changed (marked *)")


def sequential_note(summary):
    """One line on trials saved by a sequential run, or None for a fixed-n run."""
    info = summary.get('sequential')
    if not info:
        return None
    return (f"sequential: looks {', '.join(map(str, info['looks']))}, threshold {info['threshold']:.4g} per look; "
            f"{info['trials_run']} of {info['trials_planned']} planned trials run, {info['trials_saved']} saved"
            + (f", {info['trials_skipped']} skipped by a budget or stop" if info.get('trials_skipped') else ''))


def stop_superseded(record, decision):
    """True when a regrade or a correction gave a verdict the recorded stop reason contradicts."""
    reason, significant = record.get('stop_reason'), decision['verdict'] in ('regressed', 'improved')
    if reason == 'zero_tolerance':
        return not decision.get('new_zero_tolerance_failures')
    if reason in ('significant', 'futility'):
        return significant != (reason == 'significant')
    return False


def stop_text(decision):
    """Where a sequential case stopped, for example 'n=5 significant'."""
    record = (decision or {}).get('sequential') or {}
    if not record.get('stopped_at'):
        return ''
    return f"n={record['stopped_at']} {record['stop_reason']}" + (', superseded' if record.get('superseded') else '')


def arm_label(summary, arm):
    """The arm as reports show it: `none` for the NONE revision, else the arm name."""
    revision = (summary.get('revisions') or {}).get(arm) or {}
    return 'none' if revision.get('name') == NONE else arm


def markdown(summary):
    lines = ['| Case | Host | Arm | Valid | Primary pass (95% CI) | All pass | pass^k | Mean wall s | Lines added median | Cost USD | Verdict |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            verdict = verdict_text(result)
            for arm, s in result['arms'].items():
                label = arm_label(summary, arm)
                pow_k = s['pass_pow_k']
                pow_text = f"{pow_k['value']:.2f} (k={pow_k['k']})" if pow_k['value'] is not None else '-'
                lines.append(f"| {case_id} | {host} | {label} | {s['valid']}/{s['trials']} | "
                             f"{primary_text(s, entry['primary'])} | "
                             f"{s['all_pass']}/{s['valid']} | {pow_text} | {s['wall_s_mean'] or '-'} | "
                             f"{s['lines_added_median'] if s['lines_added_median'] is not None else '-'} | "
                             f"{s['cost_usd_total'] if s['cost_usd_total'] is not None else '-'} | {verdict} |")
    failures, readings = [], []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            for arm, s in result['arms'].items():
                label = arm_label(summary, arm)
                invalid = {k: v for k, v in s['statuses'].items() if k not in GRADED}
                if invalid:
                    failures.append(f'- {case_id} {host} {label}: invalid trials {invalid}')
                if s.get('isolation_warnings'):
                    failures.append(f"- {case_id} {host} {label}: {s['isolation_warnings']} trials used skills "
                                    'from outside the plugin or of unknown source, or named the project path')
            if reading(result, gate_alpha(summary)):
                readings.append(f'- {case_id} {host}: {reading(result, gate_alpha(summary))}')
    for note in (correction_note(summary), sequential_note(summary)):
        if note:
            failures.append(f'- {note}')
    return '\n'.join(lines + ([''] + failures if failures else [])
                     + (['', '**How to read the verdicts**', ''] + readings if readings else []))


FORMATS = ('table', 'markdown', 'comment')
# Verdicts in the order the comment report counts and lists them; `none` is a single-revision run.
VERDICT_ORDER = ('regressed', 'inconclusive', 'non-inferior', 'improved', 'none')
ATTENTION = ('regressed', 'inconclusive')
# Keeps a comment under the 65,536-character body limit common to code hosts, with room for the headline.
COMMENT_DETAILS_LIMIT = 50000


def comment(summary):
    """Markdown for a pull request comment or CI job summary on any platform: verdict counts, cases that
    need attention, and the full table in a collapsed block."""
    results = [(case_id, host, entry, result) for case_id, entry in summary['cases'].items()
               for host, result in entry['hosts'].items()]

    def verdict(result):
        return (result.get('decision') or {}).get('verdict', 'none')

    counts = {v: sum(verdict(r) == v for *_, r in results) for v in VERDICT_ORDER}
    lines = [f'- **{v}**: {counts[v]}' if v != 'none' else f'- **no verdict** (one revision): {counts[v]}'
             for v in VERDICT_ORDER if counts[v]] or ['- no results']
    attention = sorted((r for r in results if verdict(r[3]) in ATTENTION),
                       key=lambda r: ATTENTION.index(verdict(r[3])))
    if attention:
        lines += ['', '#### Needs attention', '']
        for case_id, host, entry, result in attention:
            meaning = result.get('interpretation') or {}  # Summaries from older versions lack it.
            detail = ' '.join(t for t in (meaning.get('explanation'), meaning.get('recommendation')) if t)
            lines.append(f"- **{verdict_text(result)}** `{case_id}` on {host}" + (f': {detail}' if detail else ''))
    if summary.get('stopped_early'):
        lines[:0] = ['**The run stopped early: these are partial results.**', '']
    for note in (correction_note(summary), sequential_note(summary)):
        if note:
            lines += ['', f'{note[0].upper()}{note[1:]}.']
    total = summary.get('cost_usd_total')
    if total is not None:
        lines += ['', f'Total host-reported cost: {total:.2f} USD']
    full = markdown(summary)
    if len(full) > COMMENT_DETAILS_LIMIT:
        full = ('The full results are too long for one comment. Read them with `seatrial report --format '
                'markdown`, or in `summary.json`.')
    lines += ['', '<details>', '<summary>Full results</summary>', '', full, '', '</details>']
    return '\n'.join(lines)


def render(summary, fmt):
    """The summary in one text format: table, markdown, or comment."""
    return {'markdown': markdown, 'comment': comment}.get(fmt, terminal)(summary)


def terminal(summary):
    """The summary as an aligned, colored table for a terminal."""
    from seatrial import ui
    rows, notes = [], []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            decision = result.get('decision') or {}
            for arm, s in result['arms'].items():
                label = arm_label(summary, arm)
                pow_k = s['pass_pow_k']
                rows.append([case_id, ui.style(host, 'cyan'), label, f"{s['valid']}/{s['trials']}",
                             primary_text(s, entry['primary']), f"{s['all_pass']}/{s['valid']}",
                             f"{pow_k['value']:.2f}" if pow_k['value'] is not None else '-',
                             s['wall_s_mean'] or '-',
                             '-' if s['cost_usd_total'] is None else f"{s['cost_usd_total']:.2f}",
                             ui.style(verdict_text(result), ui.VERDICT_STYLE.get(decision['verdict'], 'gray'))
                             if decision and arm == 'head' else ''])
                invalid = {k: v for k, v in s['statuses'].items() if k not in GRADED}
                if invalid:
                    notes.append(ui.warn(f'{case_id} {host} {label}: invalid trials {invalid}'))
                if s.get('isolation_warnings'):
                    notes.append(ui.warn(f"{case_id} {host} {label}: {s['isolation_warnings']} trials used skills "
                                         'from outside the plugin or of unknown source, or named the project path'))
            text = reading(result, gate_alpha(summary)) or decision.get('reason')  # Older summaries lack a reading.
            if text:
                notes.append(ui.info(f'{case_id} {host}: {text}'))
    for note in (correction_note(summary), sequential_note(summary)):
        if note:
            notes.append(ui.info(note))
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
