"""Report formats and multiple-comparison correction, built from hand-written summaries; no runs."""
import json
import unittest
from statistics import median
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


class NoiseFloorTests(unittest.TestCase):
    def test_label_compares_the_mounted_files_or_the_git_tree(self):
        def run(base, head, arms=('base', 'head')):
            return report.label({'arms': list(arms), 'revisions': {'base': base, 'head': head}})
        commit = {'name': 'main', 'sha': 'a1', 'tree': 't1', 'files': 'files:f1'}
        self.assertEqual(run(commit, dict(commit, name='HEAD')), 'a/a')
        self.assertEqual(run(commit, {'name': 'WORKTREE', 'tree': 'files:f1', 'files': 'files:f1'}), 'a/a')
        self.assertEqual(run({'name': 'v1', 'tree': 't1'}, {'name': 'v2', 'tree': 't1'}), 'a/a')  # Older summaries.
        self.assertEqual(run(commit, dict(commit, tree='t2', files='files:f2')), 'a/b')
        self.assertEqual(run({'name': 'NONE', 'tree': None}, {'name': 'NONE', 'tree': None}), 'a/a')
        self.assertEqual(run({'name': 'NONE', 'tree': None}, commit), 'a/b')
        self.assertIsNone(report.label({'arms': ['run'], 'revisions': {'run': commit}}))
        self.assertEqual(run(commit, dict(commit, name='v2', tree='t2')), 'a/b')  # Same files, another file mode.

    def test_a_comparison_with_too_few_valid_trials_has_no_drift(self):
        data = c_summary([(9, 9)])
        data['revisions'] = {'base': {'name': 'main', 'tree': 't'}, 'head': {'name': 'main', 'tree': 't'}}
        data['cases']['demo-000']['hosts']['fake']['arms']['head'] = c_arm(1, 0)
        data = report.correct(data, 'none')
        noise = data['cases']['demo-000']['hosts']['fake']['noise']
        self.assertEqual((noise['difference'], noise['noisy']), (None, False))
        self.assertEqual(report.noise_reference([('aa', data)])['cases'], {})

    def test_an_a_a_run_measures_drift_and_marks_significant_verdicts_noisy(self):
        data = c_summary([(9, 9), (9, 2), (5, 3)])
        data['revisions'] = {'base': {'name': 'main', 'tree': 't'}, 'head': {'name': 'main', 'tree': 't'}}
        data = report.correct(data, 'none')
        self.assertEqual(data['label'], 'a/a')
        noise = {case_id: entry['hosts']['fake']['noise'] for case_id, entry in data['cases'].items()}
        self.assertEqual([n['verdict'] for n in noise.values()], ['non-inferior', 'regressed', 'inconclusive'])
        self.assertEqual([n['noisy'] for n in noise.values()], [False, True, False])
        self.assertEqual(noise['demo-001']['difference'], round(7 / 9, 4))
        self.assertEqual(data['aa'], {'comparisons': 3, 'noisy': ['demo-001 fake'], 'max_difference': round(7 / 9, 4)})
        self.assertEqual([case_id for case_id, _ in report.case_order(data)], ['demo-001', 'demo-000', 'demo-002'])
        # Changing revisions drops the A/A fields on the next correction.
        data['revisions']['head']['tree'] = 'u'
        data = report.correct(data, 'none')
        self.assertEqual(data['label'], 'a/b')
        self.assertNotIn('aa', data)
        self.assertFalse(any('noise' in entry['hosts']['fake'] for entry in data['cases'].values()))

    def test_a_reference_reads_each_change_against_the_largest_drift_and_rejects_a_b_runs(self):
        same = {'base': {'name': 'main', 'tree': 't'}, 'head': {'name': 'main', 'tree': 't'}}
        first = report.correct(dict(c_summary([(9, 7), (9, 9)]), revisions=same), 'none')
        second = report.correct(dict(c_summary([(9, 6), (9, 9)]), revisions=same), 'none')
        reference = report.noise_reference([('one', first), ('two', second)])
        self.assertEqual(reference['cases']['demo-000']['fake'], {'difference': round(3 / 9, 4), 'noisy': False, 'runs': 2})
        data = c_summary([(9, 6), (9, 5)])
        data['noise_reference'] = reference
        data = report.correct(data, 'none')
        floors = [entry['hosts']['fake']['noise_floor'] for entry in data['cases'].values()]
        self.assertEqual([f['within'] for f in floors], [True, False])
        self.assertIn('within that noise floor', data['cases']['demo-000']['hosts']['fake']['interpretation']['explanation'])
        self.assertIn(', beyond noise', report.verdict_text(data['cases']['demo-001']['hosts']['fake']))
        self.assertEqual(report.noise_note(data),
                         'read against the noise floor of 2 A/A runs: 1 of 2 primary changes within it')
        with self.assertRaisesRegex(ValueError, 'not an A/A run'):
            report.noise_reference([('ab', data)])


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

    def test_effect_shows_next_to_the_verdict_in_every_format(self):
        data = summary(broken=comparison(arm(), arm(behavior=7)))
        self.assertIn('| −30 pp (−60 to +4) | inconclusive, underpowered |', report.markdown(data))
        self.assertIn('−30 pp (−60 to +4)', report.terminal(data))
        self.assertIn('`broken` on fake, primary −30 pp (−60 to +4):', report.comment(data))

    def test_single_revision_and_older_summaries_render(self):
        old = arm()
        del old['primary_ci']
        text = report.comment(summary(solo={'arms': {'head': old}}))
        self.assertTrue(text.startswith('- **no verdict** (one revision): 1\n'))
        self.assertIn('| solo | fake | head | 10/10 | 10/10 |', text)
        self.assertNotIn('Needs attention', text)


def cost_summary(base_costs, head_costs, head_behavior=10):
    """A stored two-arm summary whose arms carry per-trial costs; correct() adds the cost note."""
    def cost_arm(behavior, costs):
        a = arm(behavior=behavior)
        a.update(cost_usd_values=costs, cost_usd_median=median(costs) if costs else None)
        for name in ('num_turns', 'tool_errors', 'wall_s'):
            a[f'{name}_values'] = []
        return a
    base, head = cost_arm(10, base_costs), cost_arm(head_behavior, head_costs)
    entry = {'assertions': ASSERTIONS, 'primary': 'behavior',
             'hosts': {'fake': {'arms': {'base': base, 'head': head},
                                'efficiency': report.efficiency(base, head)}}}
    data = report.correct({'n': 10, 'arms': ['base', 'head'], 'cases': {'demo': entry}}, 'none')
    return data, data['cases']['demo']['hosts']['fake']


class CostTests(unittest.TestCase):
    def test_a_significantly_cheaper_non_inferior_head_is_cheaper(self):
        data, result = cost_summary([0.20] * 10, [0.10] * 10)
        self.assertEqual(result['decision']['verdict'], 'non-inferior')
        self.assertEqual(result['decision']['cost'], 'cheaper')
        self.assertEqual(result['efficiency']['cost_usd']['head_median'], 0.10)
        self.assertLess(result['efficiency']['cost_usd']['p_value'], 0.05)
        self.assertIn('Head cost less: median 0.1 against 0.2 USD', result['interpretation']['explanation'])
        self.assertIn('non-inferior, cheaper', report.markdown(data))
        self.assertTrue(report.comment(data).startswith('- **non-inferior**: 1 (1 cheaper)\n'))

    def test_costlier_is_a_note_on_any_verdict_and_never_changes_it(self):
        data, result = cost_summary([0.10] * 10, [0.20] * 10, head_behavior=3)
        self.assertEqual((result['decision']['verdict'], result['decision']['cost']), ('regressed', 'costlier'))
        self.assertIn('regressed, costlier', report.verdict_text(result))
        # A cheaper head that regressed gets no note: cheaper only qualifies non-inferior.
        _, result = cost_summary([0.20] * 10, [0.10] * 10, head_behavior=3)
        self.assertEqual((result['decision']['verdict'], result['decision']['cost']), ('regressed', None))

    def test_missing_or_close_costs_add_no_note(self):
        for base, head in (([], []), ([0.2] * 10, []), ([0.2, 0.1] * 5, [0.1, 0.2] * 5)):
            data, result = cost_summary(base, head)
            self.assertIsNone(result['decision']['cost'])
            self.assertEqual(result['decision']['verdict'], 'non-inferior')
        self.assertIsNone(result['efficiency']['num_turns']['p_value'])
        # Summaries written before efficiency metrics have no comparison and no note.
        old = report.correct(c_summary([(9, 9)]), 'none')
        self.assertIsNone(old['cases']['demo-000']['hosts']['fake']['decision']['cost'])
        self.assertIn('| - | - | - | +0 pp', report.markdown(summary(same=comparison(arm(), arm()))))

    def test_arm_summaries_take_efficiency_values_from_valid_trials(self):
        class Case:
            assertions, primary = {'behavior': 'rate'}, 'behavior'
        records = [{'status': 'completed', 'cost_usd': 0.1, 'usage': {'num_turns': 4}, 'tool_errors': 1,
                    'wall_s': 10.0, 'assertions': [{'id': 'behavior', 'passed': True}], 'pass': True},
                   {'status': 'completed', 'cost_usd': 0.3, 'usage': None, 'tool_errors': None, 'wall_s': 20.0,
                    'assertions': [{'id': 'behavior', 'passed': True}], 'pass': True},
                   {'status': 'timeout', 'cost_usd': 5.0, 'usage': {'num_turns': 99}, 'tool_errors': 9,
                    'wall_s': 600.0}]
        s = report.summarize_arm(Case, records)
        self.assertEqual(s['cost_usd_values'], [0.1, 0.3])
        self.assertAlmostEqual(s['cost_usd_median'], 0.2)
        self.assertEqual((s['num_turns_values'], s['tool_errors_values']), ([4], [1]))
        self.assertEqual(s['wall_s_median'], 15.0)
        self.assertEqual(s['cost_usd_total'], 5.4)  # The total still counts every trial's spend.

    def test_the_note_follows_the_rank_shift_when_the_medians_disagree(self):
        # Head costs rank higher overall, yet the head median is lower.
        data, result = cost_summary([0.10] * 16 + [0.20] * 24, [0.15] * 24 + [0.30] * 16)
        cost = result['efficiency']['cost_usd']
        self.assertLess(cost['head_median'], cost['base_median'])
        self.assertEqual(cost['shift'], 'higher')
        self.assertLess(cost['p_value'], 0.05)
        self.assertEqual(result['decision']['cost'], 'costlier')

    def test_summaries_without_a_shift_fall_back_to_the_medians(self):
        decision = {'verdict': 'non-inferior', 'p_values': {}}
        comparison = {'cost_usd': {'base_median': 0.2, 'head_median': 0.1, 'p_value': 0.01}}
        self.assertEqual(report.cost_note(decision, comparison), 'cheaper')

    def test_small_median_costs_stay_visible_in_every_format(self):
        data, _ = cost_summary([0.004] * 10, [0.002] * 10)
        data['cost_usd_total'] = 0.06
        self.assertIn('0.0020', report.terminal(data))
        self.assertIn('Total host-reported cost: 0.06 USD', report.markdown(data))
        self.assertEqual(report.comment(data).count('Total host-reported cost'), 1)


if __name__ == '__main__':
    unittest.main()
