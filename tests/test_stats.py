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

    def test_wilson_matches_reference_values(self):
        for (c, n), (low, high) in (((7, 10), (0.3968, 0.8922)), ((5, 10), (0.2366, 0.7634)),
                                    ((1, 3), (0.0615, 0.7923)), ((0, 10), (0.0, 0.2775)),
                                    ((10, 10), (0.7225, 1.0))):
            with self.subTest(c=c, n=n):
                self.assertAlmostEqual(stats.wilson(c, n)[0], low, places=4)
                self.assertAlmostEqual(stats.wilson(c, n)[1], high, places=4)
        self.assertEqual(stats.wilson(0, 4)[0], 0.0)
        self.assertEqual(stats.wilson(4, 4)[1], 1.0)
        self.assertLess(stats.wilson(7, 10, z=1.0)[1], stats.wilson(7, 10)[1])
        self.assertIsNone(stats.wilson(0, 0))

    def test_trials_needed_and_detectable_change(self):
        self.assertEqual(stats.trials_needed(0.6, 0.8), 82)  # Standard two-proportion sample size.
        self.assertEqual(stats.trials_needed(0.8, 0.6), 82)
        self.assertIsNone(stats.trials_needed(0.7, 0.7))
        self.assertIsNone(stats.detectable_change(0.5, 10))
        self.assertEqual(stats.detectable_change(0.5, 11), 0.5)
        self.assertEqual(stats.detectable_change(0.7, 10), 0.59)
        self.assertEqual(stats.detectable_change(0.5, 100), 0.2)

    def test_interpretation_explains_the_verdict_without_changing_it(self):
        def read(base, head):
            decision = stats.decide(ASSERTIONS, 'behavior', base, head, 10)
            return decision, stats.interpret(ASSERTIONS, 'behavior', base, head, decision)

        decision, meaning = read(arm(behavior=6), arm(behavior=8))
        self.assertEqual((decision['verdict'], meaning['verdict']), ('non-inferior', 'non-inferior'))
        self.assertTrue(meaning['underpowered'])
        self.assertEqual(meaning['trials_needed'], 82)
        self.assertIn('`--n 82`', meaning['recommendation'])
        self.assertIn('intervals overlap', meaning['explanation'])
        decision, meaning = read(arm(behavior=7), arm(behavior=7, preserved=9))
        self.assertEqual(meaning['evidence'], 'zero_tolerance')
        self.assertIsNone(meaning['recommendation'])
        decision, meaning = read(arm(behavior=5), arm(behavior=5))
        self.assertIsNone(meaning['trials_needed'])
        self.assertEqual(meaning['verdict'], 'non-inferior')
        self.assertFalse(meaning['underpowered'])
        self.assertIsNone(meaning['recommendation'])
        decision, meaning = read(arm(behavior=2, preserved=9), arm(behavior=9, preserved=9))
        self.assertEqual((meaning['verdict'], meaning['evidence']), ('inconclusive', 'zero_tolerance'))
        self.assertEqual(meaning['deciding_assertion'], 'preserved')
        self.assertEqual(meaning['recommendation'], 'Fix `preserved` first; more trials do not change this verdict.')
        self.assertFalse(meaning['underpowered'])
        decision, meaning = read(arm(behavior=3), arm(behavior=9))
        self.assertEqual((meaning['verdict'], meaning['evidence']), ('improved', 'significant'))
        self.assertEqual(meaning['primary']['head'], {'passes': 9, 'valid': 10, 'rate': 0.9,
                                                      'ci': [0.5958, 0.9821], 'margin': 0.1931})
        self.assertEqual((meaning['deciding_assertion'], round(meaning['deciding_p_value'], 3)), ('behavior', 0.02))
        self.assertFalse(meaning['underpowered'])
        many = {'base': {'valid': 1000, 'assertion_passes': {'behavior': 500, 'preserved': 1000}},
                'head': {'valid': 1000, 'assertion_passes': {'behavior': 510, 'preserved': 1000}}}
        decision = stats.decide(ASSERTIONS, 'behavior', many['base'], many['head'], 10)
        meaning = stats.interpret(ASSERTIONS, 'behavior', many['base'], many['head'], decision)
        self.assertTrue(meaning['underpowered'])
        self.assertIn('needs more than 1000 trials', meaning['recommendation'])
        self.assertIn('at 1000 trials this run detects changes of about 7 points', meaning['recommendation'])
        decision, meaning = read(arm(valid=5), arm())
        self.assertEqual(meaning['evidence'], 'insufficient_trials')
        self.assertIn('fewer than 8 valid trials', meaning['explanation'])

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

    def test_threshold_uses_full_precision_p_values(self):
        # 6/9 against 1/9 has p = 0.04977; rounded to 0.050 it would miss the threshold.
        def decide(base, head):
            return stats.decide(ASSERTIONS, 'behavior', arm(9, behavior=base), arm(9, behavior=head), 9)
        regressed, improved = decide(6, 1), decide(1, 6)
        self.assertEqual((regressed['verdict'], improved['verdict']), ('regressed', 'improved'))
        self.assertAlmostEqual(regressed['p_values']['behavior'], 0.0497738, places=6)
        # 7/9 against 2/9 has p = 0.0567: just above the threshold in both directions.
        self.assertEqual(decide(7, 2)['verdict'], 'inconclusive')
        self.assertEqual(decide(2, 7)['verdict'], 'non-inferior')


if __name__ == '__main__':
    unittest.main()
