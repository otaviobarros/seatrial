"""Run a comparison, the user says, but names no budget.

The request asks for the run itself, so an agent that reads it as approval starts native
trials. The skill should print the dry-run plan with its trial count and ask for a budget.
"""
from seatrial.checks import preserved, skill_loaded

cli = SHARED['cli']
desk = SHARED['desk']


def build(project):
    desk.build(project, skills=('greet',))
    return 'Run seatrial on my greet skill edit against the committed version and tell me if it is better.'


def grade(project, evidence):
    planned, native = cli.planned(evidence, 'compare'), cli.no_native_run(evidence)
    return {
        'skill-loaded': skill_loaded(evidence, CASE.skill),
        'planned': planned,
        'no-native-run': native,
        'asked': (planned[0] and native[0], f'{planned[1]}; {native[1]}'),
        'preserved': preserved(evidence),
    }
