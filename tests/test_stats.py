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

    def test_rate_difference_matches_newcombe_method_10(self):
        # Newcombe (1998), Table II, method 10: published as first minus second proportion.
        published = (((56, 70), (48, 80), 0.0524, 0.3339), ((9, 10), (3, 10), 0.1705, 0.8090),
                     ((5, 56), (0, 29), -0.0381, 0.1926), ((0, 10), (0, 20), -0.1611, 0.2775),
                     ((0, 10), (0, 10), -0.2775, 0.2775), ((10, 10), (0, 20), 0.6791, 1.0),
                     ((10, 10), (0, 10), 0.6075, 1.0))
        for (c2, n2), (c1, n1), low, high in published:
            estimate, lo, hi = stats.rate_difference(c1, n1, c2, n2)  # Base first, head second.
            self.assertAlmostEqual(estimate, c2 / n2 - c1 / n1)
            self.assertAlmostEqual(lo, low, places=4)
            self.assertAlmostEqual(hi, high, places=4)
        # c = 0 and c = n in either arm keep the interval inside [-1, 1] with nonzero width.
        estimate, lo, hi = stats.rate_difference(10, 10, 0, 10)
        self.assertEqual(estimate, -1.0)
        self.assertAlmostEqual(lo, -1.0)
        self.assertAlmostEqual(hi, -0.6075, places=4)
        self.assertIsNone(stats.rate_difference(0, 0, 1, 1))
        self.assertEqual(stats.effect_text(stats.effect(10, 10, 7, 10)), '−30 pp (−60 to +4)')
        self.assertEqual(stats.effect_text(stats.effect(5, 10, 9, 10)), '+40 pp (−0.2 to +68)')
        self.assertEqual(stats.effect_text(stats.effect(1, 5, 4, 5)), '+60 pp (−0.03 to +83)')
        self.assertEqual(stats.effect_text({'estimate': 0.005, 'ci': [-0.005, 0.009]}), '+0.5 pp (−0.5 to +0.9)')

    def test_rank_shift_names_where_head_values_rank(self):
        self.assertEqual(stats.rank_shift([1, 2], [3, 4]), 'higher')
        self.assertEqual(stats.rank_shift([3, 4], [1, 2]), 'lower')
        self.assertEqual(stats.rank_shift([1, 3], [3, 1]), 'none')
        self.assertIsNone(stats.rank_shift([], [1]))

    def test_decisions_report_the_effect_of_every_assertion(self):
        decision = stats.decide(ASSERTIONS, 'behavior', arm(behavior=10), arm(behavior=7), 10)
        self.assertEqual(set(decision['effect']), set(ASSERTIONS))
        self.assertEqual(decision['effect_primary'], decision['effect']['behavior'])
        entry = decision['effect_primary']
        self.assertEqual((entry['level'], entry['metric']), (0.95, 'rate_difference'))
        self.assertAlmostEqual(entry['estimate'], -0.3)
        self.assertEqual(decision['effect']['preserved']['estimate'], 0.0)
        self.assertNotIn('effect', stats.decide(ASSERTIONS, 'behavior', arm(valid=5), arm(), 10))

    def test_trials_needed_and_detectable_change(self):
        self.assertEqual(stats.trials_needed(0.6, 0.8), 82)  # Standard two-proportion sample size.
        self.assertEqual(stats.trials_needed(0.8, 0.6), 82)
        self.assertIsNone(stats.trials_needed(0.7, 0.7))
        self.assertEqual(stats.trials_needed(0.6, 0.8, alpha=0.01), 122)  # A family of five under a correction.
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
        self.assertIsNone(meaning['trials_needed'])
        self.assertIsNone(meaning['detectable_change'])
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
        self.assertIn('at 1000 valid trials this run detects changes of about 7 points', meaning['recommendation'])
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

    def test_margin_n_keeps_the_planned_non_inferiority_margin(self):
        rate = {'behavior': 'rate'}
        base, head = {'valid': 5, 'assertion_passes': {'behavior': 5}}, {'valid': 5, 'assertion_passes': {'behavior': 3}}
        self.assertEqual(stats.decide(rate, 'behavior', base, head, 5)['verdict'], 'non-inferior')
        self.assertEqual(stats.decide(rate, 'behavior', base, head, 5, margin_n=8)['verdict'], 'inconclusive')

    def test_sequential_step_thresholds_and_stop_reasons(self):
        def step(base, head, looks=(5, 10), index=0, family=1, **zero):
            valid = looks[index]
            b = {'valid': valid, 'assertion_passes': {'behavior': base, 'preserved': valid}}
            h = {'valid': valid, 'assertion_passes': {'behavior': head, 'preserved': zero.get('preserved', valid)}}
            return stats.sequential_step(ASSERTIONS, 'behavior', b, h, list(looks), index, family=family)
        self.assertEqual(stats.look_threshold([5, 10]), 0.025)
        self.assertEqual(stats.look_threshold([3, 6, 9]), 0.05 / 3)
        self.assertEqual(step(5, 5, preserved=4)['reason'], 'zero_tolerance')
        self.assertEqual(step(0, 5)['reason'], 'significant')  # p = 0.0079 < 0.025.
        # A correction over four tests could lift p above 0.025, so the gate is 0.025 / 4.
        self.assertEqual(step(0, 5, family=4)['outcome'], 'continue')
        # Futility uses the undivided 0.025: 2/10 against 8/10 (p = 0.023) can still pass it.
        self.assertEqual(step(3, 2, family=4)['outcome'], 'continue')
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


# Penicillin and rabbits (R `mantelhaen.test` example data): per penicillin level, rabbits cured
# of all treated with no delay (here head) and with a 1.5 h delay (here base). The first and last
# levels have no variation and drop out. R gives Mantel-Haenszel X-squared = 3.9286 (p = 0.04747),
# common odds ratio 7, 95% interval 1.0267 to 47.725, and exact conditional p = 0.03994.
RABBITS = [(0, 5, 0, 6), (0, 6, 3, 6), (2, 6, 6, 6), (6, 6, 5, 6), (5, 5, 2, 2)]


class PooledTests(unittest.TestCase):
    def test_pooled_statistics_match_textbook_values(self):
        statistic, p = stats.cochran_mantel_haenszel(RABBITS)
        self.assertAlmostEqual(statistic, 3.9286, places=4)
        self.assertAlmostEqual(p, 0.04747, places=5)
        self.assertAlmostEqual(stats.stratified_exact(RABBITS), 0.03994, places=5)
        estimate, low, high = stats.mantel_haenszel_odds_ratio(RABBITS)
        self.assertAlmostEqual(estimate, 7.0)
        self.assertAlmostEqual(low, 1.0267, places=3)  # z = 1.96, as the Wilson intervals use.
        self.assertAlmostEqual(high / 47.725, 1, places=3)
        pooled = stats.pool(RABBITS)
        self.assertEqual((pooled['strata'], pooled['strata_excluded'], pooled['shift']), (3, 2, 'higher'))
        self.assertEqual((pooled['base'], pooled['head']), ({'passes': 8, 'valid': 18}, {'passes': 14, 'valid': 18}))
        self.assertEqual(pooled['expected_head_passes'], 11.0)

    def test_one_table_reduces_to_fisher(self):
        for table in ((5, 10, 8, 10), (10, 10, 5, 10), (0, 10, 10, 10), (3, 7, 9, 12), (7, 10, 3, 5)):
            with self.subTest(table=table):
                self.assertAlmostEqual(stats.stratified_exact([table]), stats.fisher(*table), places=12)
                self.assertAlmostEqual(stats.stratified_min_attainable_p([table]),
                                       stats.min_attainable_p(*table), places=12)

    def test_small_consistent_gains_pool_into_a_significant_one(self):
        tables = [(5, 10, 8, 10)] * 5
        self.assertTrue(all(stats.fisher(*t) > 0.05 for t in tables))
        pooled = stats.pool(tables)
        self.assertLess(pooled['p_value'], 0.01)
        self.assertEqual(stats.pooled_verdict(pooled, pooled['p_value']), 'improved')
        self.assertEqual(stats.pooled_verdict(pooled, pooled['p_value'], alpha=pooled['p_value'] / 2), 'inconclusive')
        lower = stats.pool([(8, 10, 5, 10)] * 5)
        self.assertEqual((lower['shift'], stats.pooled_verdict(lower, lower['p_value'])), ('lower', 'regressed'))

    def test_opposite_effects_cancel(self):
        pooled = stats.pool([(2, 10, 9, 10), (9, 10, 2, 10)])
        self.assertEqual((pooled['shift'], pooled['p_value']), ('none', 1.0))
        self.assertEqual(stats.pooled_verdict(pooled, pooled['p_value']), 'inconclusive')

    def test_tables_without_variation_or_trials_are_left_out(self):
        empty = stats.pool([(10, 10, 10, 10), (0, 10, 0, 10), (0, 0, 3, 5)])
        self.assertEqual((empty['strata'], empty['strata_excluded'], empty['p_value']), (0, 3, None))
        self.assertIsNone(empty['odds_ratio'])
        self.assertIsNone(empty['cmh'])
        self.assertEqual(stats.pooled_verdict(empty, None), 'inconclusive')
        self.assertEqual(stats.pool([(5, 10, 8, 10), (10, 10, 10, 10)])['p_value'], stats.fisher(5, 10, 8, 10))

    def test_one_sided_tables_give_an_unbounded_or_zero_odds_ratio(self):
        self.assertEqual(stats.mantel_haenszel_odds_ratio([(0, 10, 10, 10), (3, 10, 10, 10)]), (None, None, None))
        self.assertEqual(stats.mantel_haenszel_odds_ratio([(10, 10, 0, 10)]), (0.0, None, None))

    def test_many_large_strata_stay_fast(self):
        import time
        start = time.perf_counter()
        stats.pool([(40, 100, 55, 100)] * 30)
        self.assertLess(time.perf_counter() - start, 5)

    def test_pooled_false_positives_stay_at_or_below_alpha(self):
        import random
        generator, repeats, false = random.Random(7), 2000, 0
        for _ in range(repeats):
            tables = []
            for rate in (0.2, 0.5, 0.6, 0.8, 0.9):  # Cases differ in difficulty; the arms do not.
                tables.append((sum(generator.random() < rate for _ in range(10)), 10,
                               sum(generator.random() < rate for _ in range(10)), 10))
            p = stats.stratified_exact(tables)
            false += p is not None and p < 0.05
        self.assertLessEqual(false / repeats, 0.05)


if __name__ == '__main__':
    unittest.main()
