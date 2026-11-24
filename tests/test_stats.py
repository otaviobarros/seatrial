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

    def test_holm_and_benjamini_hochberg_match_reference_values(self):
        p = [0.01, 0.04, 0.03, 0.005]
        for got, want in zip(stats.holm(p), [0.03, 0.06, 0.06, 0.02]):
            self.assertAlmostEqual(got, want)
        for got, want in zip(stats.benjamini_hochberg(p), [0.02, 0.04, 0.04, 0.02]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(stats.holm([0.02, 0.02]), [0.04, 0.04])  # Ties keep their order and value.
        self.assertEqual(stats.benjamini_hochberg([0.02, 0.02]), [0.02, 0.02])
        self.assertEqual(stats.holm([0.3]), [0.3])
        self.assertEqual(stats.benjamini_hochberg([0.3]), [0.3])
        self.assertEqual(stats.holm([0.6, 0.7]), [1.0, 1.0])  # Capped at 1.
        self.assertEqual(stats.adjust([], 'holm'), [])
        self.assertEqual(stats.adjust([0.01, 0.2], 'none'), [0.01, 0.2])
        with self.assertRaises(ValueError):
            stats.adjust([0.1], 'bonferroni')

    def test_min_attainable_p_marks_tests_that_cannot_be_significant(self):
        self.assertEqual(stats.min_attainable_p(10, 10, 10, 10), 1.0)
        self.assertAlmostEqual(stats.min_attainable_p(9, 9, 9, 9), 1.0)
        self.assertAlmostEqual(stats.min_attainable_p(10, 10, 9, 10), 1.0)  # One failure anywhere: p >= 1.0.
        self.assertLess(stats.min_attainable_p(10, 10, 5, 10), 0.05)
        self.assertLess(stats.min_attainable_p(5, 10, 5, 10), 0.05)

    def test_adjusted_p_values_gate_the_verdict(self):
        base, head = arm(10, behavior=10), arm(10, behavior=5)  # Raw p = 0.0325.
        raw = stats.decide(ASSERTIONS, 'behavior', base, head, 10)
        self.assertEqual(raw['verdict'], 'regressed')
        self.assertNotIn('adjusted_p_values', raw)
        corrected = stats.decide(ASSERTIONS, 'behavior', base, head, 10,
                                 adjusted={'behavior': 0.065, 'preserved': 1.0})
        self.assertEqual(corrected['verdict'], 'inconclusive')
        self.assertEqual(corrected['p_values'], raw['p_values'])
        self.assertEqual(corrected['adjusted_p_values'], {'behavior': 0.065, 'preserved': 1.0})


    def test_sequential_step_thresholds_and_stop_reasons(self):
        def step(base, head, looks=(5, 10), index=0, **zero):
            valid = looks[index]
            b = {'valid': valid, 'assertion_passes': {'behavior': base, 'preserved': valid}}
            h = {'valid': valid, 'assertion_passes': {'behavior': head, 'preserved': zero.get('preserved', valid)}}
            return stats.sequential_step(ASSERTIONS, 'behavior', b, h, list(looks), index)
        self.assertEqual(stats.look_threshold([5, 10]), 0.025)
        self.assertEqual(stats.look_threshold([3, 6, 9]), 0.05 / 3)
        self.assertEqual(step(5, 5, preserved=4)['reason'], 'zero_tolerance')
        self.assertEqual(step(0, 5)['reason'], 'significant')  # p = 0.0079 < 0.025.
        self.assertEqual(step(5, 5)['reason'], 'futility')  # Even 5/10 against 10/10 has p = 0.0325.
        self.assertEqual(step(2, 2, looks=(3, 10))['outcome'], 'continue')  # 2/10 against 9/10 is still possible.
        last = step(7, 7, looks=(3, 10), index=1)
        self.assertEqual((last['outcome'], last['reason'], last['n']), ('stop', 'max_n', 10))
        early = step(4, 4, looks=(5, 10))
        self.assertEqual(early['threshold'], 0.025)
        self.assertEqual(set(early['p_values']), set(ASSERTIONS))
        # Too few valid trials: continue, unless this is the last look.
        few = stats.sequential_step(ASSERTIONS, 'behavior', arm(2), arm(5), [5, 10], 0)
        self.assertEqual((few['outcome'], few['p_values']), ('continue', {}))
        self.assertEqual(stats.sequential_step(ASSERTIONS, 'behavior', arm(2), arm(10), [5, 10], 1)['reason'],
                         'max_n')

    def test_sequential_runs_keep_the_false_regression_rate_under_alpha(self):
        import random
        generator, looks, repeats = random.Random(41), [3, 5, 8, 10], 2000
        false = 0
        for _ in range(repeats):
            base, head = [], []
            for index in range(len(looks)):
                while len(base) < looks[index]:
                    base.append(generator.random() < 0.6)
                    head.append(generator.random() < 0.6)
                arms = [{'valid': len(x), 'assertion_passes': {'behavior': sum(x)}} for x in (base, head)]
                step = stats.sequential_step({'behavior': 'rate'}, 'behavior', *arms, looks, index)
                if step['outcome'] == 'stop':
                    verdict = stats.decide({'behavior': 'rate'}, 'behavior', *arms, looks[index],
                                           alpha=step['threshold'])['verdict']
                    false += verdict == 'regressed'
                    break
        self.assertLessEqual(false / repeats, 0.05)


if __name__ == '__main__':
    unittest.main()
