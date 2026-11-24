"""Report formats and multiple-comparison correction, built from hand-written summaries; no runs."""
import json
import unittest
from unittest import mock

from seatrial import report, stats

ASSERTIONS = {'behavior': 'rate', 'preserved': 'zero'}


def arm(valid=10, **passes):
    counts = {a: passes.get(a, valid) for a in ASSERTIONS}
    return {'trials': valid, 'valid': valid, 'statuses': {'completed': valid}, 'assertion_passes': counts,
            'primary_ci': [round(b, 4) for b in stats.wilson(counts['behavior'], valid)],
            'all_pass': min(counts.values()), 'pass_pow_k': {'k': 3, 'value': None}, 'wall_s_mean': 1.0,
            'lines_added_median': None, 'cost_usd_total': None, 'isolation_warnings': 0}


def comparison(base, head):
    decision = stats.decide(ASSERTIONS, 'behavior', base, head, 10)
    return {'arms': {'base': base, 'head': head}, 'decision': decision,
            'interpretation': stats.interpret(ASSERTIONS, 'behavior', base, head, decision)}


def summary(**hosts):
    return {'arms': ['base', 'head'], 'cost_usd_total': None,
            'cases': {case_id: {'primary': 'behavior', 'hosts': {'fake': result}} for case_id, result in hosts.items()}}


def c_arm(valid, behavior):
    return {'valid': valid, 'assertion_passes': {'behavior': behavior, 'preserved': valid}}


def c_summary(pairs, n=9):
    """A two-arm summary with one case per (base, head) pass count on the behavior assertion."""
    cases = {}
    for index, (base, head) in enumerate(pairs):
        cases[f'demo-{index:03d}'] = {
            'assertions': {'behavior': 'rate', 'preserved': 'zero'}, 'primary': 'behavior',
            'hosts': {'fake': {'arms': {'base': c_arm(n, base), 'head': c_arm(n, head)}}}}
    return report.correct({'n': n, 'arms': ['base', 'head'], 'cases': cases}, 'none')


def verdicts(data):
    return [entry['hosts']['fake']['decision']['verdict'] for entry in data['cases'].values()]


class CorrectionTests(unittest.TestCase):
    def test_holm_removes_borderline_regressions_and_records_the_raw_verdict(self):
        data = c_summary([(6, 1)] * 5 + [(9, 9)] * 5)  # 6/9 against 1/9 has raw p = 0.0498.
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
        data = report.correct(c_summary([(9, 0), (0, 9), (6, 1)]), 'bh')
        self.assertEqual(verdicts(data)[:2], ['regressed', 'improved'])
        restored = report.correct(data, 'none')
        self.assertEqual(verdicts(restored), ['regressed', 'improved', 'regressed'])
        self.assertFalse(any('adjusted_p_values' in e['hosts']['fake']['decision'] or
                             'verdict_uncorrected' in e['hosts']['fake']['decision']
                             for e in restored['cases'].values()))

    def test_untestable_assertions_keep_their_raw_p_values(self):
        data = report.correct(c_summary([(6, 1), (9, 9)]), 'holm')
        trivial = data['cases']['demo-001']['hosts']['fake']['decision']
        self.assertEqual(trivial['adjusted_p_values'], trivial['p_values'])
        self.assertEqual(data['multiple_comparisons']['family_size'], 1)

    def test_a_contradicted_sequential_stop_is_marked_superseded(self):
        data = c_summary([(9, 0), (9, 9)])
        data['sequential'] = {'looks': [9], 'cases': {f'demo-{i:03d}': {'fake': {
            'looks': [], 'stopped_at': 9, 'stop_reason': 'significant', 'trials_saved': 0}} for i in range(2)}}
        data = report.correct(data, 'none')
        kept, stale = (data['cases'][f'demo-{i:03d}']['hosts']['fake']['decision'] for i in range(2))
        self.assertFalse(kept['sequential']['superseded'])
        self.assertTrue(stale['sequential']['superseded'])
        self.assertEqual(report.stop_text(stale), 'n=9 significant, superseded')

    def test_sequential_runs_show_the_stop_and_the_per_look_threshold_in_every_format(self):
        data = c_summary([(9, 0)])
        data['sequential'] = {'looks': [3, 6, 9], 'threshold': 0.05 / 3, 'trials_planned': 18, 'trials_run': 6,
                              'trials_saved': 12, 'cases': {'demo-000': {'fake': {
                                  'looks': [], 'stopped_at': 9, 'stop_reason': 'significant', 'trials_saved': 0}}}}
        data = report.correct(data, 'none')
        result = data['cases']['demo-000']['hosts']['fake']
        self.assertEqual(report.verdict_text(result), 'regressed (n=9 significant)')
        self.assertIn('strong evidence, p < 0.0033', report.reading(result, report.gate_alpha(data)))
        self.assertIn('less than 0.33% of the time', result['interpretation']['explanation'])
        with mock.patch.object(report, 'markdown', return_value=''):
            text = report.comment(dict(data, cost_usd_total=None))
        self.assertIn('Sequential: looks 3, 6, 9', text)

    def test_an_underpowered_sequential_run_recommends_a_fixed_n_rerun(self):
        data = c_summary([(6, 7)])
        data['sequential'] = {'looks': [3, 6, 9], 'threshold': 0.05 / 3, 'trials_planned': 18, 'trials_run': 18,
                              'trials_saved': 0, 'cases': {}}
        meaning = report.correct(data, 'none')['cases']['demo-000']['hosts']['fake']['interpretation']
        self.assertTrue(meaning['underpowered'])
        fixed = stats.trials_needed(6 / 9, 7 / 9)
        self.assertGreater(meaning['trials_needed'], fixed)  # Sized at the per-look threshold.
        self.assertIn(f'Run about {fixed} valid trials per revision in a fixed-n run without `--sequential`',
                      meaning['recommendation'])

    def test_decisions_without_p_values_stay_outside_the_family(self):
        data = c_summary([(9, 9)])
        data['cases']['demo-000']['hosts']['fake']['arms']['head'] = c_arm(3, 3)
        data = report.correct(data, 'none')
        self.assertIn('reason', data['cases']['demo-000']['hosts']['fake']['decision'])
        self.assertEqual(report.correct(data, 'holm')['multiple_comparisons']['family_size'], 0)

    def test_correction_rebuilds_the_interpretation_and_notes_the_comment(self):
        data = report.correct(c_summary([(6, 1)] * 5 + [(9, 9)] * 5), 'holm')
        result = data['cases']['demo-000']['hosts']['fake']
        meaning = result['interpretation']
        self.assertEqual(meaning['verdict'], result['decision']['verdict'])
        self.assertEqual(meaning['evidence'], 'not_significant')
        self.assertEqual(meaning['deciding_p_value'], result['decision']['adjusted_p_values']['behavior'])
        self.assertIn('adjusted p =', meaning['explanation'])
        self.assertIn('would be `regressed`', meaning['explanation'])
        self.assertEqual(meaning['alpha'], 0.01)  # 0.05 across a family of five.
        raw = report.correct(json.loads(json.dumps(data)), 'none')['cases']['demo-000']['hosts']['fake']
        self.assertGreater(meaning['trials_needed'], raw['interpretation']['trials_needed'])
        self.assertIn('*', report.verdict_text(result))
        with mock.patch.object(report, 'markdown', return_value=''):
            text = report.comment(dict(data, cost_usd_total=None))
        self.assertIn('Verdicts corrected with holm across 5 Fisher tests', text)


class CommentFormatTests(unittest.TestCase):
    def test_headline_counts_then_attention_in_order_then_details(self):
        text = report.comment(summary(
            better=comparison(arm(behavior=2), arm(behavior=9)),
            unclear=comparison(arm(valid=5), arm()),
            similar=comparison(arm(behavior=6), arm(behavior=8)),
            broken=comparison(arm(), arm(preserved=9))))
        head, _, rest = text.partition('\n\n')
        self.assertEqual(head.splitlines(), ['- **regressed**: 1', '- **inconclusive**: 1',
                                             '- **non-inferior**: 1', '- **improved**: 1'])
        attention = rest.split('<details>')[0]
        order = [attention.index(f'`{c}`') for c in ('broken', 'unclear')]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('`better`', attention)
        self.assertNotIn('`similar`', attention)
        details = text.split('<details>')[1]
        self.assertIn('non-inferior, underpowered', details)
        self.assertIn('`--n 82`', details)
        self.assertIn('<summary>Full results</summary>', details)
        self.assertIn('| better | fake | head | 10/10 | 9/10 (60–98%) |', details)
        self.assertIn('**How to read the verdicts**', details)
        self.assertTrue(text.endswith('</details>'))

    def test_stopped_early_leads_and_long_details_are_replaced(self):
        data = summary(similar=comparison(arm(behavior=6), arm(behavior=8)))
        data['stopped_early'] = True
        self.assertTrue(report.comment(data).startswith('**The run stopped early'))
        with mock.patch.object(report, 'COMMENT_DETAILS_LIMIT', 10):
            text = report.comment(data)
        self.assertIn('too long for one comment', text)
        self.assertNotIn('| similar |', text)
        self.assertIn('non-inferior, underpowered', report.terminal(data))

    def test_single_revision_and_older_summaries_render(self):
        old = arm()
        del old['primary_ci']
        text = report.comment(summary(solo={'arms': {'head': old}}))
        self.assertTrue(text.startswith('- **no verdict** (one revision): 1\n'))
        self.assertIn('| solo | fake | head | 10/10 | 10/10 |', text)
        self.assertNotIn('Needs attention', text)


if __name__ == '__main__':
    unittest.main()
