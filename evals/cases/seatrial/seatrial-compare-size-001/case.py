"""Plan a comparison meant to show that an edit helps, in a project with two skills and n = 3.

Three trials per side cannot reach `improved`, so the dry-run plan should set --n 10 or more.
It should also scope the plan to the cases the edit affects (--changed, --skill, or --case)
so that the unchanged skill's case spends no trials.
"""
from seatrial.checks import preserved, skill_loaded

cli = SHARED['cli']
desk = SHARED['desk']

SCOPES = (cli.changed_since_base, *desk.GREET_SCOPES)


def build(project):
    desk.build(project)
    return ('I edited the greet skill and want to show my team that it greets better than the committed '
            'version. Plan that comparison and tell me how many trials it needs; I will approve a budget after '
            'that, so run no trials yet. Do not change any files.')


def grade(project, evidence):
    return {
        'skill-loaded': skill_loaded(evidence, CASE.skill),
        'sized': cli.sized_plan(evidence, 'compare', 10),
        'scoped': cli.scoped_plan(evidence, 'compare', SCOPES, ('farewell',)),
        'no-native-run': cli.no_native_run(evidence),
        'preserved': preserved(evidence),
    }
