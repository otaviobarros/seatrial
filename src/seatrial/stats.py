"""Statistics for small trial counts. Verdicts are advisory, not merge gates."""
from collections import Counter
from math import ceil, comb, erfc, sqrt


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


def trials_needed(p1, p2, z_alpha=1.96, z_power=0.8416):
    """Approximate trials per arm for a two-sided 5% test to detect p1 versus p2 with 80% power.

    Normal approximation; Fisher's exact test is conservative, so treat the result as a floor.
    None when the rates are equal."""
    if p1 == p2:
        return None
    mean = (p1 + p2) / 2
    root = z_alpha * sqrt(2 * mean * (1 - mean)) + z_power * sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return ceil(root * root / (p1 - p2) ** 2)


def detectable_change(rate, n):
    """Smallest change from rate, in either direction, that n trials per arm detect with
    80% power; None when no change within 0..1 is detectable."""
    for points in range(1, 101):
        for other in (rate + points / 100, rate - points / 100):
            if 0 <= other <= 1 and trials_needed(rate, other) <= n:
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


def interpret(assertions, primary, base, head, decision):
    """Read a decide() verdict together with the primary pass rates and their 95% Wilson
    intervals, and say how many trials would settle a non-significant result.

    Advisory only: the verdict and its Fisher gate stay with decide(). Returns stable
    fields for tools plus `explanation` and `recommendation` text rendered from them."""
    nb, nh = base['valid'], head['valid']
    verdict = decision['verdict']
    b, h = rate(base['assertion_passes'][primary], nb), rate(head['assertion_passes'][primary], nh)
    result = {'verdict': verdict, 'evidence': 'insufficient_trials', 'deciding_assertion': None,
              'deciding_p_value': None,
              'primary': {'assertion': primary, 'base': b, 'head': h, 'difference': None, 'p_value': None,
                          'intervals_overlap': None},
              'underpowered': False, 'trials_needed': None, 'detectable_change': None,
              'explanation': None, 'recommendation': None}
    if decision.get('reason'):
        result['explanation'] = f"Not compared: {decision['reason']}."
        result['recommendation'] = 'Fix the causes of the invalid trials, then run again.'
        return result
    p = decision['p_values'][primary]
    n = min(nb, nh)
    overlap = b['ci'][0] <= h['ci'][1] and h['ci'][0] <= b['ci'][1]
    difference = h['rate'] - b['rate']
    needed = trials_needed(b['rate'], h['rate'])
    result['primary'].update(difference=round(difference, 4), p_value=p, intervals_overlap=overlap)
    result.update(trials_needed=needed, detectable_change=detectable_change(b['rate'], n))
    rates = f'`{primary}` passed {percent(b)} in base and {percent(h)} in head'
    if verdict == 'regressed' and decision['new_zero_tolerance_failures']:
        names = ', '.join(f'`{a}`' for a in decision['new_zero_tolerance_failures'])
        result.update(evidence='zero_tolerance', deciding_assertion=decision['new_zero_tolerance_failures'][0])
        result['explanation'] = (f'{names} passed in every base trial and failed in at least one head trial; '
                                 'one failure of a zero-tolerance assertion is a regression.')
    elif verdict in ('regressed', 'improved'):
        a = decision['significantly_worse'][0] if verdict == 'regressed' else primary
        gate = decision['p_values'][a]
        result.update(evidence='strong' if gate < 0.01 else 'significant', deciding_assertion=a,
                      deciding_p_value=gate)
        moved = (f"`{a}` fell from {percent(rate(base['assertion_passes'][a], nb))} in base to "
                 f"{percent(rate(head['assertion_passes'][a], nh))} in head" if verdict == 'regressed' else rates)
        result['explanation'] = (f'{moved}; Fisher p = {gate:.3f}: a change this large would happen by chance '
                                 f"{'less than 1%' if gate < 0.01 else 'less than 5%'} of the time if the "
                                 'revisions behaved the same.')
    elif blocking := [a for a, kind in assertions.items() if kind == 'zero' and head['assertion_passes'][a] < nh]:
        # Head fails a zero-tolerance assertion that base also failed: no regression, but no other verdict.
        names = ', '.join(f'`{a}`' for a in blocking)
        result.update(evidence='zero_tolerance', deciding_assertion=blocking[0])
        result['explanation'] = (f'{names} failed in head, and in base too, so the failure is not new; a '
                                 'zero-tolerance failure in head rules out `improved` and `non-inferior`. '
                                 f'{rates[0].upper()}{rates[1:]}; Fisher p = {p:.3f}.')
        result['recommendation'] = f'Fix {names} first; more trials do not change this verdict.'
    else:
        # Not significant. For `inconclusive`, an assertion dropped by more than two trials' worth.
        if verdict == 'inconclusive':
            a = min(assertions, key=lambda x: head['assertion_passes'][x] / nh - base['assertion_passes'][x] / nb)
            b, h = rate(base['assertion_passes'][a], nb), rate(head['assertion_passes'][a], nh)
            p, difference = decision['p_values'][a], h['rate'] - b['rate']
            needed = trials_needed(b['rate'], h['rate'])
            result.update(trials_needed=needed, detectable_change=detectable_change(b['rate'], n))
            rates = (f'`{a}` dropped from {percent(b)} in base to {percent(h)} in head, more than two trials\' '
                     'worth')
        else:
            a = primary
        result.update(evidence='not_significant', deciding_assertion=a, deciding_p_value=p)
        result['explanation'] = (f"{rates}; Fisher p = {p:.3f} is not significant, so this run cannot tell the "
                                 'revisions apart' + (f" on `{a}`." if a != primary else
                                                      f"; the 95% intervals {'overlap' if overlap else 'do not overlap'}."))
        if verdict == 'non-inferior':
            result['explanation'] += " No assertion dropped by more than two trials' worth."
        # Equal rates leave nothing to size: a clean regression guard already holds.
        result['underpowered'] = needed is not None and needed > n
    if result['underpowered'] and needed > 1000:
        smallest = result['detectable_change']
        result['recommendation'] = (f'A {difference * 100:+.0f}-point change needs more than 1000 trials per '
                                    'revision to detect with 80% power'
                                    + (f'; at {n} trials this run detects changes of about {smallest * 100:.0f} '
                                       'points or more.' if smallest else '.'))
    elif result['underpowered']:
        result['recommendation'] = (f'Run about {needed} trials per revision (`--n {needed}`; raise `[limits] '
                                    f'max_n` first if it is lower) to detect a {difference * 100:+.0f}-point '
                                    'change with 80% power.')
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


def decide(assertions, primary, base, head, n):
    """Compare two arms of one case on one host.

    base and head hold 'valid' (graded trials) and 'assertion_passes' ({id: count}).
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
    worse = [a for a in assertions if bp[a] / nb > hp[a] / nh and p[a] < 0.05]
    zero_fail_head = any(hp[a] < nh for a, kind in assertions.items() if kind == 'zero')
    if new_zero or worse:
        verdict = 'regressed'
    elif hp[primary] / nh > bp[primary] / nb and p[primary] < 0.05 and not zero_fail_head:
        verdict = 'improved'
    elif not zero_fail_head and all(hp[a] / nh >= bp[a] / nb - 2 / n for a in assertions):
        verdict = 'non-inferior'
    else:
        verdict = 'inconclusive'
    return {'verdict': verdict, 'p_values': p, 'new_zero_tolerance_failures': new_zero,
            'significantly_worse': worse}
