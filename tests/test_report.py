"""Multiple-comparison correction over a stored summary."""
import json
import unittest

from seatrial import report


def arm(valid, behavior):
    return {'valid': valid, 'assertion_passes': {'behavior': behavior, 'preserved': valid}}


def summary(pairs, n=9):
    """A two-arm summary with one case per (base, head) pass count on the behavior assertion."""
    cases = {}
    for index, (base, head) in enumerate(pairs):
        cases[f'demo-{index:03d}'] = {
            'assertions': {'behavior': 'rate', 'preserved': 'zero'}, 'primary': 'behavior',
            'hosts': {'fake': {'arms': {'base': arm(n, base), 'head': arm(n, head)}}}}
    return report.correct({'n': n, 'arms': ['base', 'head'], 'cases': cases}, 'none')


def verdicts(data):
    return [entry['hosts']['fake']['decision']['verdict'] for entry in data['cases'].values()]


class CorrectionTests(unittest.TestCase):
    def test_holm_removes_borderline_regressions_and_records_the_raw_verdict(self):
        data = summary([(6, 1)] * 5 + [(9, 9)] * 5)  # 6/9 against 1/9 has raw p = 0.0498.
        self.assertEqual(verdicts(data).count('regressed'), 5)
        # 9/9 against 9/9 cannot reach p < 0.05, so only the five 6/9 against 1/9 tests count.
        self.assertEqual(data['multiple_comparisons'], {'method': 'none', 'family_size': 5, 'alpha': 0.05})
        corrected = report.correct(json.loads(json.dumps(data)), 'holm')
        self.assertEqual(verdicts(corrected).count('regressed'), 0)
        changed = [e['hosts']['fake']['decision'] for e in corrected['cases'].values()
                   if 'verdict_uncorrected' in e['hosts']['fake']['decision']]
        self.assertEqual(len(changed), 5)
        self.assertTrue(all(d['verdict_uncorrected'] == 'regressed' for d in changed))
        self.assertTrue(all(d['adjusted_p_values']['behavior'] > d['p_values']['behavior'] for d in changed))
        self.assertEqual(corrected['multiple_comparisons']['method'], 'holm')
        self.assertIn('changed (marked *)', report.correction_note(corrected))

    def test_strong_effects_survive_and_none_restores_raw_verdicts(self):
        data = report.correct(summary([(9, 0), (0, 9), (6, 1)]), 'bh')
        self.assertEqual(verdicts(data)[:2], ['regressed', 'improved'])
        restored = report.correct(data, 'none')
        self.assertEqual(verdicts(restored), ['regressed', 'improved', 'regressed'])
        self.assertFalse(any('adjusted_p_values' in e['hosts']['fake']['decision'] or
                             'verdict_uncorrected' in e['hosts']['fake']['decision']
                             for e in restored['cases'].values()))

    def test_untestable_assertions_keep_their_raw_p_values(self):
        data = report.correct(summary([(6, 1), (9, 9)]), 'holm')
        trivial = data['cases']['demo-001']['hosts']['fake']['decision']
        self.assertEqual(trivial['adjusted_p_values'], trivial['p_values'])
        self.assertEqual(data['multiple_comparisons']['family_size'], 1)

    def test_a_contradicted_sequential_stop_is_marked_superseded(self):
        data = summary([(9, 0), (9, 9)])
        data['sequential'] = {'looks': [9], 'cases': {f'demo-{i:03d}': {'fake': {
            'looks': [], 'stopped_at': 9, 'stop_reason': 'significant', 'trials_saved': 0}} for i in range(2)}}
        data = report.correct(data, 'none')
        kept, stale = (data['cases'][f'demo-{i:03d}']['hosts']['fake']['decision'] for i in range(2))
        self.assertFalse(kept['sequential']['superseded'])
        self.assertTrue(stale['sequential']['superseded'])
        self.assertEqual(report.stop_text(stale), 'n=9 significant, superseded')

    def test_decisions_without_p_values_stay_outside_the_family(self):
        data = summary([(9, 9)])
        data['cases']['demo-000']['hosts']['fake']['arms']['head']['valid'] = 3
        data = report.correct(data, 'none')
        self.assertIn('reason', data['cases']['demo-000']['hosts']['fake']['decision'])
        self.assertEqual(report.correct(data, 'holm')['multiple_comparisons']['family_size'], 0)


if __name__ == '__main__':
    unittest.main()
