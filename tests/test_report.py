"""Report formats built from hand-written summaries; no runs."""
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
