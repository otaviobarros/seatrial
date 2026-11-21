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
    p = {a: round(fisher(bp[a], nb, hp[a], nh), 3) for a in assertions}
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
