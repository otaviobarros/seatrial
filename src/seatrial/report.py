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
    text += f", {decision['cost']}" if decision.get('cost') else ''
    stopped = stop_text(decision)
    text += f' ({stopped})' if stopped else ''
    return f'{text}, underpowered' if (result.get('interpretation') or {}).get('underpowered') else text


# Metrics compared between arms over valid trials, with the Mann-Whitney test lines_added uses.
EFFICIENCY = ('cost_usd', 'num_turns', 'tool_errors', 'wall_s')


def metric(record, name):
    """One efficiency value of a record, or None when the host did not report it."""
    value = (record.get('usage') or {}).get('num_turns') if name == 'num_turns' else record.get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def efficiency(base, head):
    """Per metric: both arms' medians and the Mann-Whitney p-value (None when an arm lacks values)."""
    result = {}
    for name in EFFICIENCY:
        b, h = base.get(f'{name}_values') or [], head.get(f'{name}_values') or []
        result[name] = {'base_median': median(b) if b else None, 'head_median': median(h) if h else None,
                        'shift': stats.rank_shift(b, h), 'p_value': stats.mann_whitney(b, h)}
    return result


def cost_note(decision, comparison):
    """'cheaper' for a non-inferior head with significantly lower cost, 'costlier' for any verdict with
    significantly higher cost, else None. Tested at 0.05 without correction; never changes the verdict."""
    cost = (comparison or {}).get('cost_usd') or {}
    if 'p_values' not in decision or cost.get('p_value') is None or cost['p_value'] >= stats.ALPHA:
        return None
    # The direction comes from the ranks the test compared; summaries written before `shift` use the medians.
    shift = cost.get('shift') or (None if cost['head_median'] == cost['base_median'] else
                                  'lower' if cost['head_median'] < cost['base_median'] else 'higher')
    if shift == 'lower':
        return 'cheaper' if decision['verdict'] == 'non-inferior' else None
    return 'costlier' if shift == 'higher' else None


def effect_text(result):
    """The primary effect with its 95% interval, for example `−30 pp (−60 to +4)`, or '-'."""
    entry = (result.get('decision') or {}).get('effect_primary')
    return stats.effect_text(entry) if entry else '-'


def median_text(s, name, digits=None):
    value = s.get(f'{name}_median')  # Missing in summaries written before efficiency metrics.
    if value is None:
        return '-'
    return f'{value:.{digits}f}' if digits is not None else f'{value:g}'


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
    measured = {}
    for name in EFFICIENCY:
        values = [v for v in (metric(r, name) for r in valid) if v is not None]
        measured[f'{name}_values'], measured[f'{name}_median'] = values, median(values) if values else None
    return {'trials': len(records), 'valid': len(valid), 'statuses': statuses,
            'assertion_passes': passes, 'assertion_ci': ci, 'primary_ci': ci[case.primary], 'all_pass': all_pass,
            'pass_at_k': {'k': k, 'value': stats.pass_at_k(len(valid), all_pass, k) if k else None},
            'pass_pow_k': {'k': k, 'value': stats.pass_pow_k(len(valid), all_pass, k) if k else None},
            'lines_added_values': added, 'lines_added_median': median(added) if added else None,
            'wall_s_mean': _mean([r['wall_s'] for r in records if r.get('wall_s') is not None]),
            'commands_mean': _mean([r['commands'] for r in valid if r.get('commands') is not None]),
            'final_words_mean': _mean([r['final_words'] for r in valid if 'final_words' in r]),
            'cost_usd_total': round(sum(costs), 4) if costs else None,
            'isolation_warnings': sum(1 for r in records if r.get('foreign_skills') or r.get('unknown_skills') or r.get('outside_access')),
            **measured}


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
                result['efficiency'] = efficiency(arms['base'], arms['head'])
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
        result = summary['cases'][case_id]['hosts'][host]
        decision['cost'] = cost_note(decision, result.get('efficiency'))
        result['decision'] = decision
    # Trial counts use a Bonferroni bound on the per-test threshold: conservative for holm and bh.
    test_alpha = alpha / len(tests) if method != 'none' and tests else alpha
    rerun_alpha = test_alpha * stats.ALPHA / alpha if looks else None  # The same design without looks.
    for case_id, host in plain:
        entry = summary['cases'][case_id]
        result = entry['hosts'][host]
        result['interpretation'] = stats.interpret(entry['assertions'], entry['primary'], result['arms']['base'],
                                                   result['arms']['head'], result['decision'],
                                                   alpha=alpha, test_alpha=test_alpha, rerun_alpha=rerun_alpha)
        if result['decision']['cost']:
            cost = result['efficiency']['cost_usd']
            result['interpretation']['explanation'] += (
                f" Head cost {'less' if result['decision']['cost'] == 'cheaper' else 'more'}: median "
                f"{cost['head_median']:.4g} against {cost['base_median']:.4g} USD per trial, Mann-Whitney "
                f"p = {cost['p_value']:.3f}.")
    summary['multiple_comparisons'] = {'method': method, 'family_size': len(tests), 'alpha': alpha}
    pool_skills(summary, method, alpha)
    if 'correction' in summary:  # Run metadata; keep it in step when report or regrade changes the method.
        summary['correction'] = method
    return summary


def pool_skills(summary, method, alpha):
    """Add `skills`: one pooled verdict per skill across its cases and hosts, on each case's primary
    assertion, from the exact stratified test. Reported, never gated.

    A comparison whose decision has a `reason` (too few valid trials) stays out, and so does one
    with all passes or all failures in both arms; both are listed under `excluded`. The pooled
    tests form their own correction family, one test per skill, with the same method, and leave
    out a skill whose margins cannot reach p < alpha. Cases without a skill, from older
    summaries, are skipped."""
    skills, tables = {}, {}
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            arms, decision = result['arms'], result.get('decision')
            if not entry.get('skill') or set(arms) != {'base', 'head'} or not decision:
                continue
            item = skills.setdefault(entry['skill'], {'cases': [], 'excluded': [], 'case_verdicts': {}})
            verdicts = item['case_verdicts']
            verdicts[decision['verdict']] = verdicts.get(decision['verdict'], 0) + 1
            primary = entry['primary']
            table = (arms['base']['assertion_passes'][primary], arms['base']['valid'],
                     arms['head']['assertion_passes'][primary], arms['head']['valid'])
            if decision.get('reason'):
                item['excluded'].append({'case': case_id, 'host': host, 'reason': 'insufficient_trials'})
            elif not stats.informative(table):
                item['excluded'].append({'case': case_id, 'host': host, 'reason': 'no_variation'})
            else:
                item['cases'].append({'case': case_id, 'host': host, 'assertion': primary,
                                      'base': list(table[:2]), 'head': list(table[2:]),
                                      'shift': 'higher' if table[2] / table[3] > table[0] / table[1] else
                                      'lower' if table[2] / table[3] < table[0] / table[1] else 'none'})
                tables.setdefault(entry['skill'], []).append(table)
    family = []
    for name, item in skills.items():
        pooled = stats.pool(tables.get(name, []))
        floor = pooled.pop('min_attainable_p')
        directions = [c['shift'] for c in item['cases']]
        item.update(pooled, strata_excluded=len(item['excluded']),
                    directions={d: directions.count(d) for d in ('higher', 'lower', 'none')})
        if floor is not None and floor < alpha:
            family.append(name)
    adjusted = dict(zip(family, stats.adjust([skills[name]['p_value'] for name in family], method)))
    for name, item in skills.items():
        verdict = stats.pooled_verdict(item, item['p_value'], alpha)
        if method != 'none' and item['p_value'] is not None:
            item['adjusted_p_value'] = adjusted.get(name, item['p_value'])
            corrected = stats.pooled_verdict(item, item['adjusted_p_value'], alpha)
            if corrected != verdict:
                item['verdict_uncorrected'] = verdict
            verdict = corrected
        item['verdict'] = verdict
        item.update(alpha=alpha, family_size=len(family))
    summary['skills'] = dict(sorted(skills.items()))
    return summary


def ratio_text(odds):
    """A pooled odds ratio with its interval, for example `4 (95% CI 1.65–9.72)`; `unbounded` when no
    table has a head failure beside a base pass."""
    if not odds:
        return '-'
    if odds['estimate'] is None:
        return 'unbounded'

    def number(x):  # Three significant digits, so a bound near 1 stays visible; below 0.01, one digit (1e-5).
        return f'{x:.0f}' if x >= 100 else f'{x:.3g}' if x >= 0.01 else f'{x:.1g}'.replace('e-0', 'e-')

    text = number(odds['estimate'])
    return text + (f" (95% CI {number(odds['ci'][0])}–{number(odds['ci'][1])})" if odds.get('ci') else '')


def pooled_lines(summary, code=True):
    """One line per skill with a pooled verdict, or [] when the summary has none; code=False leaves
    the skill name without Markdown backticks, for a terminal."""
    lines = []
    for skill, item in (summary.get('skills') or {}).items():
        name = f'`{skill}`' if code else skill
        verdict = item['verdict'] + (f" (without correction: {item['verdict_uncorrected']})"
                                     if 'verdict_uncorrected' in item else '')
        if not item['strata']:
            lines.append(f"{name}: {verdict}; no case can inform the pooled test "
                         f"({len(item['excluded'])} excluded).")
            continue
        p = item.get('adjusted_p_value', item['p_value'])
        p_label = 'adjusted p' if 'adjusted_p_value' in item else 'exact p'
        counts = ', '.join(f"{n} {d}" for d, n in item['directions'].items() if n)
        text = (f"{name}: {verdict} across {item['strata']} case-host pair"
                f"{'s' if item['strata'] != 1 else ''} ({counts}); primary passed "
                f"{item['base']['passes']}/{item['base']['valid']} in base and "
                f"{item['head']['passes']}/{item['head']['valid']} in head; odds ratio "
                f"{ratio_text(item['odds_ratio'])}, {p_label} = {p:.3g}")
        if item['excluded']:
            text += f"; {len(item['excluded'])} excluded"
        lines.append(text + '.')
    return lines


def correction_note(summary):
    """One line naming the correction, or None when verdicts use raw p-values."""
    info = summary.get('multiple_comparisons') or {}
    compared = any(result.get('decision') for entry in summary['cases'].values()
                   for result in entry['hosts'].values())
    if info.get('method', 'none') == 'none' or not compared:  # A single-revision run has no verdicts.
        return None
    changed = sum(1 for entry in summary['cases'].values() for result in entry['hosts'].values()
                  if 'verdict_uncorrected' in (result.get('decision') or {}))
    text = (f"verdicts corrected with {info['method']} across {info['family_size']} Fisher tests; "
            f"{changed} changed (marked *)")
    skills = [item for item in (summary.get('skills') or {}).values() if 'adjusted_p_value' in item]
    if skills and skills[0]['family_size']:  # An empty family adjusts nothing.
        moved = sum('verdict_uncorrected' in item for item in skills)
        text += (f"; pooled verdicts corrected across {skills[0]['family_size']} skill tests, {moved} changed")
    return text


def sequential_note(summary):
    """One line on trials saved by a sequential run, or None for a fixed-n run."""
    info = summary.get('sequential')
    if not info:
        return None
    family = info.get('stop_family') or 1
    gate = f" (stops at {info['threshold'] / family:.4g} across {family} tests)" if family > 1 else ''
    return (f"sequential: looks {', '.join(map(str, info['looks']))}, threshold {info['threshold']:.4g} per look"
            f"{gate}; {info['trials_run']} of {info['trials_planned']} planned trials run, {info['trials_saved']} saved"
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


def markdown(summary, notes=True):
    """The summary as a Markdown table; notes=False leaves out run notes a caller prints itself."""
    lines = ['| Case | Host | Arm | Valid | Primary pass (95% CI) | All pass | pass^k | Mean wall s | Lines added median '
             '| Cost USD median | Turns median | Tool errors median | Effect (95% CI) | Verdict |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            verdict, change = verdict_text(result), effect_text(result)
            for arm, s in result['arms'].items():
                label = arm_label(summary, arm)
                pow_k = s['pass_pow_k']
                pow_text = f"{pow_k['value']:.2f} (k={pow_k['k']})" if pow_k['value'] is not None else '-'
                lines.append(f"| {case_id} | {host} | {label} | {s['valid']}/{s['trials']} | "
                             f"{primary_text(s, entry['primary'])} | "
                             f"{s['all_pass']}/{s['valid']} | {pow_text} | {s['wall_s_mean'] or '-'} | "
                             f"{s['lines_added_median'] if s['lines_added_median'] is not None else '-'} | "
                             f"{median_text(s, 'cost_usd', 4)} | {median_text(s, 'num_turns')} | "
                             f"{median_text(s, 'tool_errors')} | {change} | {verdict} |")
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
    total = summary.get('cost_usd_total') if notes else None  # comment() prints the total itself.
    pooled = [f'- {line}' for line in pooled_lines(summary)] if notes else []  # comment() prints them itself.
    notes = [n for n in (correction_note(summary), sequential_note(summary)) if n] if notes else []
    lines += ['', f'Total host-reported cost: {total:.2f} USD'] if total is not None else []
    return '\n'.join(lines + ([''] + failures if failures else []) + [x for n in notes for x in ('', f'{n[0].upper()}{n[1:]}.')]
                     + (['', '**Pooled by skill** (reported, not gated)', ''] + pooled if pooled else [])
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

    def notes(v):
        found = [(r.get('decision') or {}).get('cost') for *_, r in results if verdict(r) == v]
        parts = [f'{found.count(note)} {note}' for note in ('cheaper', 'costlier') if found.count(note)]
        return f" ({', '.join(parts)})" if parts else ''

    lines = [f'- **{v}**: {counts[v]}{notes(v)}' if v != 'none' else f'- **no verdict** (one revision): {counts[v]}'
             for v in VERDICT_ORDER if counts[v]] or ['- no results']
    attention = sorted((r for r in results if verdict(r[3]) in ATTENTION),
                       key=lambda r: ATTENTION.index(verdict(r[3])))
    if attention:
        lines += ['', '#### Needs attention', '']
        for case_id, host, entry, result in attention:
            meaning = result.get('interpretation') or {}  # Summaries from older versions lack it.
            detail = ' '.join(t for t in (meaning.get('explanation'), meaning.get('recommendation')) if t)
            change = effect_text(result)
            lines.append(f"- **{verdict_text(result)}** `{case_id}` on {host}"
                         + (f', primary {change}' if change != '-' else '') + (f': {detail}' if detail else ''))
    pooled = pooled_lines(summary)
    if pooled:
        lines += ['', '#### Pooled by skill', ''] + [f'- {line}' for line in pooled]
    if summary.get('stopped_early'):
        lines[:0] = ['**The run stopped early: these are partial results.**', '']
    for note in (correction_note(summary), sequential_note(summary)):
        if note:
            lines += ['', f'{note[0].upper()}{note[1:]}.']
    total = summary.get('cost_usd_total')
    if total is not None:
        lines += ['', f'Total host-reported cost: {total:.2f} USD']
    full = markdown(summary, notes=False)  # The headline already carries the notes.
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
                             s['wall_s_mean'] or '-', median_text(s, 'cost_usd', 4), median_text(s, 'num_turns'),
                             median_text(s, 'tool_errors'), effect_text(result) if decision and arm == 'head' else '',
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
    notes += [ui.info(f'pooled by skill (reported, not gated): {line}') for line in pooled_lines(summary, code=False)]
    text = ui.table(['Case', 'Host', 'Arm', 'Valid', 'Primary', 'All', 'pass^k', 'Wall s', 'USD med', 'Turns',
                     'Tool err', 'Effect', 'Verdict'], rows)
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
