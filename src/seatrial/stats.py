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


ALPHA = 0.05
CORRECTIONS = ('none', 'holm', 'bh')


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


def decide(assertions, primary, base, head, n, adjusted=None):
    """Compare two arms of one case on one host.

    base and head hold 'valid' (graded trials) and 'assertion_passes' ({id: count}).
    adjusted maps each assertion to a corrected p-value; when given, the Fisher
    thresholds below use it in place of the raw p-value.
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
    worse = [a for a in assertions if bp[a] / nb > hp[a] / nh and gate[a] < ALPHA]
    zero_fail_head = any(hp[a] < nh for a, kind in assertions.items() if kind == 'zero')
    if new_zero or worse:
        verdict = 'regressed'
    elif hp[primary] / nh > bp[primary] / nb and gate[primary] < ALPHA and not zero_fail_head:
        verdict = 'improved'
    elif not zero_fail_head and all(hp[a] / nh >= bp[a] / nb - 2 / n for a in assertions):
        verdict = 'non-inferior'
    else:
        verdict = 'inconclusive'
    result = {'verdict': verdict, 'p_values': p, 'new_zero_tolerance_failures': new_zero,
              'significantly_worse': worse}
    if adjusted is not None:
        result['adjusted_p_values'] = dict(adjusted)
    return result
