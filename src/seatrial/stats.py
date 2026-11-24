"""Statistics for small trial counts. Verdicts are advisory, not merge gates."""
from collections import Counter
from math import ceil, comb, erfc, sqrt
from statistics import NormalDist

ALPHA = 0.05
CORRECTIONS = ('none', 'holm', 'bh')


def pass_at_k(n, c, k):
    """Chance that at least one of k trials passes, estimated from c passes in n trials."""
    if n == 0 or k > n:
        return None
    if n - c < k:
        return 1.0
    return 1 - comb(n - c, k) / comb(n, k)


def pass_pow_k(n, c, k):
    """Chance that all k trials pass (pass^k), estimated from c passes in n trials."""
    if n == 0 or k > n:
        return None
    return comb(c, k) / comb(n, k)


def wilson(c, n, z=1.96):
    """Wilson score interval (low, high) for c passes in n trials; None when n is 0."""
    if n == 0:
        return None
    rate, z2 = c / n, z * z
    centre = (rate + z2 / (2 * n)) / (1 + z2 / n)
    half = z / (1 + z2 / n) * sqrt(rate * (1 - rate) / n + z2 / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def trials_needed(p1, p2, alpha=ALPHA, z_power=0.8416):
    """Approximate trials per arm for a two-sided test at alpha to detect p1 versus p2 with 80% power.

    Normal approximation; Fisher's exact test is conservative, so treat the result as a floor.
    None when the rates are equal."""
    if p1 == p2:
        return None
    mean = (p1 + p2) / 2
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    root = z_alpha * sqrt(2 * mean * (1 - mean)) + z_power * sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return ceil(root * root / (p1 - p2) ** 2)


def detectable_change(rate, n, alpha=ALPHA):
    """Smallest change from rate that n trials per arm detect at alpha with 80% power, in at least one
    direction (a gain from a high rate may not fit below 1); None when no change within 0..1 is."""
    for points in range(1, 101):
        for other in (rate + points / 100, rate - points / 100):
            if 0 <= other <= 1 and trials_needed(rate, other, alpha) <= n:
                return points / 100
    return None


# Evidence levels, from the rule that set the verdict. Stable values for tools that parse summaries.
EVIDENCE = ('zero_tolerance', 'strong', 'significant', 'not_significant', 'insufficient_trials')


def rate(c, n):
    """Pass count, rate, 95% Wilson interval, and margin (half the interval width) for c of n."""
    if not n:
        return {'passes': c, 'valid': n, 'rate': None, 'ci': None, 'margin': None}
    low, high = wilson(c, n)
    return {'passes': c, 'valid': n, 'rate': round(c / n, 4), 'ci': [round(low, 4), round(high, 4)],
            'margin': round((high - low) / 2, 4)}


def percent(r):
    """A rate() as text, for example `7/10 (40–89%)`."""
    text = f"{r['passes']}/{r['valid']}"
    return f"{text} ({r['ci'][0] * 100:.0f}–{r['ci'][1] * 100:.0f}%)" if r.get('ci') else text


def interpret(assertions, primary, base, head, decision, alpha=ALPHA, test_alpha=None, rerun_alpha=None):
    """Read a decide() verdict together with the primary pass rates and their 95% Wilson
    intervals, and say how many trials would settle a non-significant result.

    Advisory only: the verdict and its Fisher gate stay with decide(). Evidence uses the gate
    p-value: the adjusted one when a correction ran. test_alpha is the per-test threshold for
    trial counts, for example alpha divided by the correction family. Returns stable fields
    for tools plus `explanation` and `recommendation` text rendered from them. rerun_alpha, for a
    sequential run, sizes the recommended rerun as a fixed-n run at that threshold."""
    test_alpha = test_alpha or alpha
    nb, nh = base['valid'], head['valid']
    verdict = decision['verdict']
    b, h = rate(base['assertion_passes'][primary], nb), rate(head['assertion_passes'][primary], nh)
    result = {'verdict': verdict, 'evidence': 'insufficient_trials', 'deciding_assertion': None,
              'deciding_p_value': None,
              'primary': {'assertion': primary, 'base': b, 'head': h, 'difference': None, 'p_value': None,
                          'intervals_overlap': None},
              'underpowered': False, 'trials_needed': None, 'detectable_change': None,
              'alpha': round(test_alpha, 6), 'explanation': None, 'recommendation': None}
    if decision.get('reason'):
        result['explanation'] = f"Not compared: {decision['reason']}."
        result['recommendation'] = 'Fix the causes of the invalid trials, then run again.'
        return result
    adjusted = decision.get('adjusted_p_values') or {}

    def gate(a):
        return adjusted.get(a, decision['p_values'][a])

    def p_text(a):
        raw = decision['p_values'][a]
        changed = a in adjusted and adjusted[a] != raw
        return f'Fisher p = {raw:.3f}' + (f', adjusted p = {adjusted[a]:.3f}' if changed else '')

    p = gate(primary)
    n = min(nb, nh)
    overlap = b['ci'][0] <= h['ci'][1] and h['ci'][0] <= b['ci'][1]
    difference = h['rate'] - b['rate']
    needed = trials_needed(b['rate'], h['rate'], test_alpha)
    result['primary'].update(difference=round(difference, 4), p_value=p, intervals_overlap=overlap)
    result.update(trials_needed=needed, detectable_change=detectable_change(b['rate'], n, test_alpha))
    rates = f'`{primary}` passed {percent(b)} in base and {percent(h)} in head'
    if verdict == 'regressed' and decision['new_zero_tolerance_failures']:
        names = ', '.join(f'`{a}`' for a in decision['new_zero_tolerance_failures'])
        result.update(evidence='zero_tolerance', deciding_assertion=decision['new_zero_tolerance_failures'][0])
        result['explanation'] = (f'{names} passed in every base trial and failed in at least one head trial; '
                                 'one failure of a zero-tolerance assertion is a regression.')
    elif verdict in ('regressed', 'improved'):
        a = decision['significantly_worse'][0] if verdict == 'regressed' else primary
        strong = gate(a) < alpha / 5
        result.update(evidence='strong' if strong else 'significant', deciding_assertion=a,
                      deciding_p_value=gate(a))
        moved = (f"`{a}` fell from {percent(rate(base['assertion_passes'][a], nb))} in base to "
                 f"{percent(rate(head['assertion_passes'][a], nh))} in head" if verdict == 'regressed' else rates)
        result['explanation'] = (f'{moved}; {p_text(a)}: a change this large would happen by chance less '
                                 f'than {(alpha / 5 if strong else alpha) * 100:.2g}% of the time if the '
                                 'revisions behaved the same.')
    elif blocking := [a for a, kind in assertions.items() if kind == 'zero' and head['assertion_passes'][a] < nh]:
        # Head fails a zero-tolerance assertion that base also failed: no regression, but no other verdict.
        names = ', '.join(f'`{a}`' for a in blocking)
        result.update(evidence='zero_tolerance', deciding_assertion=blocking[0], trials_needed=None,
                      detectable_change=None)
        result['explanation'] = (f'{names} failed in head, and in base too, so the failure is not new; a '
                                 'zero-tolerance failure in head rules out `improved` and `non-inferior`. '
                                 f'{rates[0].upper()}{rates[1:]}; {p_text(primary)}.')
        result['recommendation'] = f'Fix {names} first; more trials do not change this verdict.'
    else:
        # Not significant. For `inconclusive`, an assertion dropped by more than two trials' worth.
        if verdict == 'inconclusive':
            a = min(assertions, key=lambda x: head['assertion_passes'][x] / nh - base['assertion_passes'][x] / nb)
            b, h = rate(base['assertion_passes'][a], nb), rate(head['assertion_passes'][a], nh)
            p, difference = gate(a), h['rate'] - b['rate']
            needed = trials_needed(b['rate'], h['rate'], test_alpha)
            result.update(trials_needed=needed, detectable_change=detectable_change(b['rate'], n, test_alpha))
            rates = (f'`{a}` dropped from {percent(b)} in base to {percent(h)} in head, more than two trials\' '
                     'worth')
        else:
            a = primary
        result.update(evidence='not_significant', deciding_assertion=a, deciding_p_value=p)
        result['explanation'] = (f"{rates}; {p_text(a)} is not significant, so this run cannot tell the "
                                 'revisions apart' + (f" on `{a}`." if a != primary else
                                                      f"; the 95% intervals {'overlap' if overlap else 'do not overlap'}."))
        if verdict == 'non-inferior':
            result['explanation'] += " No assertion dropped by more than two trials' worth."
        # Equal rates leave nothing to size: a clean regression guard already holds.
        result['underpowered'] = needed is not None and needed > n
    if 'verdict_uncorrected' in decision:
        result['explanation'] += (f" Without the multiple-comparison correction the verdict would be "
                                  f"`{decision['verdict_uncorrected']}`.")
    threshold = f' at alpha {test_alpha:.3g}' if test_alpha != ALPHA else ''
    rerun = ''
    if result['underpowered'] and rerun_alpha:
        # More looks shrink the per-look threshold, so a larger sequential run would stay underpowered.
        needed = trials_needed(b['rate'], h['rate'], rerun_alpha)
        threshold = f' at alpha {rerun_alpha:.3g}' if rerun_alpha != ALPHA else ''
        rerun = ' in a fixed-n run without `--sequential`'
    if result['underpowered'] and needed > 1000:
        smallest = result['detectable_change']
        result['recommendation'] = (f'A {difference * 100:+.0f}-point change needs more than 1000 trials per '
                                    f'revision to detect with 80% power{threshold}'
                                    + (f'; at {n} valid trials this run detects changes of about '
                                       f'{smallest * 100:.0f} points or more, in at least one direction.'
                                       if smallest else '.'))
    elif result['underpowered']:
        result['recommendation'] = (f'Run about {needed} valid trials per revision{rerun} (`--n {needed}` or more, '
                                    'since invalid trials do not count; raise `[limits] max_n` first if it is '
                                    f'lower) to detect a {difference * 100:+.0f}-point change with 80% power{threshold}.')
    return result


def fisher(a, n1, b, n2):
    """Two-sided Fisher exact p-value for a/n1 versus b/n2 successes."""
    total, k = n1 + n2, a + b
    if total == 0:
        return 1.0

    def probability(x):
        return comb(n1, x) * comb(n2, k - x) / comb(total, k)

    observed = probability(a)
    return min(1.0, sum(probability(x) for x in range(max(0, k - n2), min(n1, k) + 1)
                        if probability(x) <= observed * (1 + 1e-9)))


def min_attainable_p(a, n1, b, n2):
    """The smallest Fisher p-value any table with these margins can reach.

    A test whose minimum is at or above alpha can never be significant; leaving it
    out of a correction family keeps the correction from over-penalizing (Tarone).
    The hypergeometric is unimodal, so the minimum sits at one of the two extreme tables."""
    k = a + b
    low, high = max(0, k - n2), min(n1, k)
    return min(fisher(low, n1, k - low, n2), fisher(high, n1, k - high, n2))


def mann_whitney(base, head):
    """Two-sided rank test with normal approximation, ties, and continuity correction."""
    if not base or not head:
        return None
    counts = Counter(base + head)
    ranks, position = {}, 1
    for value, count in sorted(counts.items()):
        ranks[value] = position + (count - 1) / 2
        position += count
    nb, nh = len(base), len(head)
    total = nb + nh
    u = sum(ranks[v] for v in base) - nb * (nb + 1) / 2
    ties = sum(t ** 3 - t for t in counts.values())
    variance = nb * nh / 12 * (total + 1 - ties / (total * (total - 1)))
    if variance == 0:
        return 1.0
    distance = max(0, abs(u - nb * nh / 2) - 0.5)
    return erfc(distance / sqrt(2 * variance))


def holm(p_values):
    """Holm step-down adjusted p-values, in input order; controls the family-wise error rate."""
    m = len(p_values)
    adjusted, running = [0.0] * m, 0.0
    for rank, index in enumerate(sorted(range(m), key=lambda i: p_values[i])):
        running = max(running, min(1.0, (m - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def benjamini_hochberg(p_values):
    """Benjamini-Hochberg adjusted p-values, in input order; controls the false discovery rate."""
    m = len(p_values)
    adjusted, running = [0.0] * m, 1.0
    for rank, index in reversed(list(enumerate(sorted(range(m), key=lambda i: p_values[i]), start=1))):
        running = min(running, m * p_values[index] / rank)
        adjusted[index] = min(1.0, running)
    return adjusted


def adjust(p_values, method):
    """Adjusted p-values for one family of tests; method is one of CORRECTIONS."""
    if method == 'holm':
        return holm(p_values)
    if method == 'bh':
        return benjamini_hochberg(p_values)
    if method == 'none':
        return list(p_values)
    raise ValueError(f'unknown correction {method!r}; choose one of {", ".join(CORRECTIONS)}')


def decide(assertions, primary, base, head, n, adjusted=None, alpha=ALPHA, margin_n=None):
    """Compare two arms of one case on one host.

    base and head hold 'valid' (graded trials) and 'assertion_passes' ({id: count}).
    adjusted maps each assertion to a corrected p-value; when given, the Fisher
    thresholds below use it in place of the raw p-value. alpha replaces 0.05, for
    example with the per-look threshold of a sequential run. margin_n sets the
    non-inferiority margin when it differs from n, such as the planned n of a case
    that stopped early.
    - regressed: a zero-tolerance assertion newly fails, or any assertion is
      significantly worse (Fisher p < 0.05).
    - improved: the primary assertion is significantly better and head has no
      zero-tolerance failure.
    - non-inferior: no assertion drops by more than two trials' worth.
    - inconclusive: otherwise, or fewer than 80% of trials are valid in an arm.
    """
    minimum = max(1, ceil(0.8 * n))
    if base['valid'] < minimum or head['valid'] < minimum:
        return {'verdict': 'inconclusive', 'reason': f'fewer than {minimum} valid trials in an arm'}
    nb, nh = base['valid'], head['valid']
    bp, hp = base['assertion_passes'], head['assertion_passes']
    p = {a: fisher(bp[a], nb, hp[a], nh) for a in assertions}  # Full precision; round only for display.
    new_zero = [a for a, kind in assertions.items() if kind == 'zero' and hp[a] < nh and bp[a] == nb]
    gate = adjusted if adjusted is not None else p
    worse = [a for a in assertions if bp[a] / nb > hp[a] / nh and gate[a] < alpha]
    zero_fail_head = any(hp[a] < nh for a, kind in assertions.items() if kind == 'zero')
    if new_zero or worse:
        verdict = 'regressed'
    elif hp[primary] / nh > bp[primary] / nb and gate[primary] < alpha and not zero_fail_head:
        verdict = 'improved'
    elif not zero_fail_head and all(hp[a] / nh >= bp[a] / nb - 2 / (margin_n or n) for a in assertions):
        verdict = 'non-inferior'
    else:
        verdict = 'inconclusive'
    result = {'verdict': verdict, 'p_values': p, 'new_zero_tolerance_failures': new_zero,
              'significantly_worse': worse}
    if adjusted is not None:
        result['adjusted_p_values'] = dict(adjusted)
    return result


def look_threshold(looks, alpha=ALPHA):
    """Bonferroni alpha spending: each look tests at alpha / number of looks."""
    return alpha / len(looks)


def sequential_step(assertions, primary, base, head, looks, index, alpha=ALPHA, family=1):
    """Decide whether one case and host stops after look `index` of a sequential run.

    Returns {n, p_values, threshold, outcome, reason}; outcome is 'stop' or 'continue',
    and reason is zero_tolerance, significant, futility, or max_n when it stops.
    `family` is the most tests a later correction can adjust across; the stop gate divides
    the threshold by it, so a Holm or BH adjusted p-value still passes after a stop.
    Futility asks whether the remaining trials could still move a Fisher test past the
    undivided per-look threshold, the loosest one a correction can apply; it does not
    wait for a later zero-tolerance failure."""
    look, last = looks[index], index == len(looks) - 1
    reachable = look_threshold(looks, alpha)
    threshold = reachable / family
    step = {'n': look, 'p_values': {}, 'threshold': threshold, 'outcome': 'continue', 'reason': None}

    def stop(reason):
        return dict(step, outcome='stop', reason=reason)

    minimum = max(1, ceil(0.8 * look))
    if base['valid'] < minimum or head['valid'] < minimum:
        return stop('max_n') if last else step
    nb, nh = base['valid'], head['valid']
    bp, hp = base['assertion_passes'], head['assertion_passes']
    step['p_values'] = {a: fisher(bp[a], nb, hp[a], nh) for a in assertions}
    if any(kind == 'zero' and hp[a] < nh and bp[a] == nb for a, kind in assertions.items()):
        return stop('zero_tolerance')
    if decide(assertions, primary, base, head, look, alpha=threshold)['verdict'] in ('regressed', 'improved'):
        return stop('significant')
    if last:
        return stop('max_n')
    more = looks[-1] - look
    can_improve = fisher(bp[primary], nb + more, hp[primary] + more, nh + more) < reachable and \
        (hp[primary] + more) / (nh + more) > bp[primary] / (nb + more)
    can_regress = any(fisher(bp[a] + more, nb + more, hp[a], nh + more) < reachable and
                      (bp[a] + more) / (nb + more) > hp[a] / (nh + more) for a in assertions)
    return step if can_improve or can_regress else stop('futility')
