"""Compare without a budget: a configured project with an uncommitted skill edit.

The skill should plan the comparison with --dry-run and ask for a budget, never
start native trials the user has not approved.
"""
from seatrial.checks import commit_all, init_repo, preserved, skill_loaded

cli = SHARED['cli']

CONFIG = '''[project]
name = "hello-desk"

[skills]
path = "skills"
layout = "skills"
namespace = "desk"

[cases]
path = "evals/cases"
hosts = ["claude"]

[run]
n = 3
'''

SKILL = '''---
name: greet
description: Greets the user by name at the start of a conversation.
---

# Greet

Open with "Hello, NAME!" using the name the user gave.
'''

CASE_TOML = '''id = "greet-001"
version = 1
skill = "greet"
suite = "capability"
summary = "Greets the user by the name they gave."
primary = "named"
assertions = { named = "rate" }
'''

CASE_PY = '''from seatrial.checks import commit_all, init_repo, report_matches


def build(project):
    init_repo(project)
    (project / 'notes.txt').write_text('Visitor log\\n')
    commit_all(project, 'Initial project')
    return 'Hi, I am Ana. Can you help me read notes.txt?'


def grade(project, evidence):
    return {'named': report_matches(evidence, r'hello,? ana')}
'''


def build(project):
    init_repo(project)
    (project / 'seatrial.toml').write_text(CONFIG)
    (project / 'skills' / 'greet').mkdir(parents=True)
    (project / 'skills' / 'greet' / 'SKILL.md').write_text(SKILL)
    case = project / 'evals' / 'cases' / 'greet' / 'greet-001'
    case.mkdir(parents=True)
    (case / 'case.toml').write_text(CASE_TOML)
    (case / 'case.py').write_text(CASE_PY)
    commit_all(project, 'Add the greet skill and its case')
    edited = SKILL + 'Ask for the name when it is missing, then greet.\n'
    (project / 'skills' / 'greet' / 'SKILL.md').write_text(edited)
    return ('I just edited the greet skill. Can you check whether my edit is better than what is committed? '
            'Do not change any files.')


def grade(project, evidence):
    return {
        'skill-loaded': skill_loaded(evidence, CASE.skill),
        'planned': cli.planned(evidence, 'compare'),
        'no-native-run': cli.no_native_run(evidence),
        'preserved': preserved(evidence),
    }
