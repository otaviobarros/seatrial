"""Statistics: reference values and the comparison verdict rule."""
import unittest

from seatrial import stats

ASSERTIONS = {'behavior': 'rate', 'preserved': 'zero'}


def arm(valid=10, **passes):
    return {'valid': valid, 'assertion_passes': {a: passes.get(a, valid) for a in ASSERTIONS}}


class StatsTests(unittest.TestCase):
    def test_fisher_matches_reference_values(self):
        for a, b, p in ((2, 8, 0.023), (3, 9, 0.020), (3, 8, 0.070), (0, 5, 0.033)):
            self.assertAlmostEqual(stats.fisher(a, 10, b, 10), p, places=3)
        self.assertEqual(stats.fisher(5, 10, 5, 10), 1.0)
        self.assertEqual(stats.fisher(0, 0, 0, 0), 1.0)

    def test_mann_whitney_reference_values_and_ties(self):
        self.assertAlmostEqual(stats.mann_whitney([1, 2, 3], [4, 5, 6]), 0.08085559837005224)
        self.assertAlmostEqual(stats.mann_whitney([1, 2, 2], [3, 3, 4]), 0.07219819770165753)
        self.assertEqual(stats.mann_whitney([2, 2], [2, 2, 2]), 1.0)
        self.assertEqual(stats.mann_whitney([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertIsNone(stats.mann_whitney([], [1]))
        self.assertEqual(stats.mann_whitney([1], [2]), 1.0)
        self.assertLess(stats.mann_whitney([1] * 10, [9] * 10), 0.001)
        self.assertEqual(stats.mann_whitney([1, 2, 2], [3, 3, 4]), stats.mann_whitney([3, 3, 4], [1, 2, 2]))

    def test_pass_at_k_and_pass_pow_k(self):
        self.assertEqual(stats.pass_at_k(3, 1, 3), 1.0)
        self.assertAlmostEqual(stats.pass_at_k(4, 1, 2), 0.5)
        self.assertEqual(stats.pass_pow_k(3, 3, 3), 1.0)
        self.assertEqual(stats.pass_pow_k(3, 2, 3), 0.0)
        self.assertAlmostEqual(stats.pass_pow_k(4, 3, 2), 0.5)
        self.assertIsNone(stats.pass_at_k(0, 0, 1))
        self.assertIsNone(stats.pass_pow_k(2, 2, 3))

    def test_decision_rule(self):
        def verdict(base, head, n=10):
            return stats.decide(ASSERTIONS, 'behavior', base, head, n)['verdict']
        self.assertEqual(verdict(arm(behavior=2), arm(behavior=8)), 'improved')
        self.assertEqual(verdict(arm(behavior=8), arm(behavior=2)), 'regressed')
        self.assertEqual(verdict(arm(), arm(preserved=9)), 'regressed')  # A zero-tolerance assertion newly fails.
        self.assertEqual(verdict(arm(behavior=7), arm(behavior=6)), 'non-inferior')
        self.assertEqual(verdict(arm(behavior=3), arm(behavior=8)), 'non-inferior')  # Better, not significant.
        self.assertEqual(verdict(arm(behavior=8), arm(behavior=5)), 'inconclusive')
        self.assertEqual(verdict(arm(7), arm()), 'inconclusive')  # Under 80% valid trials.
        self.assertEqual(verdict(arm(2), arm(3), 3), 'inconclusive')
        self.assertEqual(verdict(arm(3), arm(3), 3), 'non-inferior')


if __name__ == '__main__':
    unittest.main()
