import unittest

from seatrial.checks import UNRECORDED, steps_at_most, tool_calls, tool_order, tool_used
from seatrial.ports import new_evidence


def evidence(*calls):
    found = new_evidence()
    found['tools'] = [{'name': name, 'input': data} for name, data in calls]
    return found


TRACE = evidence(('mcp__tracker__get_issue', {'key': 'DEMO-1'}),
                 ('Bash', {'command': 'make check'}),
                 ('mcp__tracker__update_issue', {'key': 'DEMO-1', 'status': 'done'}),
                 ('Bash', {'command': 'git commit -m fix'}))


class ToolUsedTests(unittest.TestCase):
    def test_pass_fail_and_empty(self):
        self.assertTrue(tool_used(TRACE, 'mcp__tracker__get_issue')[0])
        self.assertFalse(tool_used(TRACE, 'Write')[0])
        self.assertEqual(tool_used(evidence(), 'Bash'), (False, 'no matching call'))

    def test_matching_by_field_pattern_or_function(self):
        self.assertTrue(tool_used(TRACE, 'Bash', {'command': r'^git commit'})[0])
        self.assertFalse(tool_used(TRACE, 'Bash', {'command': r'pytest'})[0])
        self.assertTrue(tool_used(TRACE, 'Bash', lambda data: 'make' in data['command'])[0])

    def test_a_collection_names_tools_across_hosts(self):
        self.assertTrue(tool_used(TRACE, {'Bash', 'shell'}, {'command': 'make check'})[0])

    def test_a_call_without_the_field_does_not_match(self):
        self.assertFalse(tool_used(TRACE, 'Bash', {'cwd': r'.*'})[0])

    def test_evidence_without_tools_fails_every_helper(self):
        old = new_evidence()
        del old['tools']
        self.assertEqual(tool_calls(old, 'Bash'), [])
        self.assertEqual(tool_used(old, 'Bash'), (False, UNRECORDED))
        self.assertEqual(tool_order(old, 'Bash', 'Write'), (False, UNRECORDED))
        self.assertEqual(steps_at_most(old, 10), (False, UNRECORDED))


class ToolOrderTests(unittest.TestCase):
    def test_pass_when_every_later_call_follows_the_first(self):
        passed, note = tool_order(TRACE, 'mcp__tracker__get_issue', 'mcp__tracker__update_issue')
        self.assertTrue(passed, note)

    def test_fail_when_a_later_call_comes_first(self):
        passed, note = tool_order(TRACE, 'Bash', 'mcp__tracker__get_issue')
        self.assertFalse(passed)
        self.assertIn('mcp__tracker__get_issue', note)

    def test_fail_when_the_first_tool_never_ran(self):
        self.assertFalse(tool_order(TRACE, 'mcp__chat__approve', 'mcp__tracker__update_issue')[0])

    def test_matching_selects_the_calls_on_each_side(self):
        self.assertTrue(tool_order(TRACE, 'Bash', 'Bash', first_matching={'command': 'make check'},
                                   then_matching={'command': 'git commit'})[0])
        self.assertFalse(tool_order(TRACE, 'Bash', 'Bash', first_matching={'command': 'git commit'},
                                    then_matching={'command': 'make check'})[0])

    def test_a_call_matching_both_sides_is_not_after_itself(self):
        trace = evidence(('Bash', {'command': 'make test && git commit -m fix'}))
        self.assertFalse(tool_order(trace, 'Bash', 'Bash', first_matching={'command': 'test'},
                                    then_matching={'command': 'commit'})[0])

    def test_pass_without_later_calls_and_on_empty_evidence(self):
        self.assertTrue(tool_order(TRACE, 'Bash', 'Write')[0])
        self.assertTrue(tool_order(evidence(), 'Read', 'Write')[0])


class StepsAtMostTests(unittest.TestCase):
    def test_pass_fail_and_empty(self):
        self.assertEqual(steps_at_most(TRACE, 4), (True, '4 tool calls'))
        self.assertEqual(steps_at_most(TRACE, 3), (False, '4 tool calls'))
        self.assertEqual(steps_at_most(evidence(), 0), (True, '0 tool calls'))


if __name__ == '__main__':
    unittest.main()
