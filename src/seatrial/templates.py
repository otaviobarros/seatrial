"""File templates for `init` and `new-case`."""

CASE_TOML = '''id = "{id}"
version = 1
skill = "{skill}"
suite = "capability"
summary = "{summary}"
rules = []
primary = "{primary}"
# rate: compared as a pass rate. zero: any failure is a regression.
assertions = {{ {primary} = "rate", preserved = "zero" }}
'''

CASE_PY = '''"""TODO: describe the scenario and what a passing trial does."""
from seatrial.checks import commit_all, copy_fixture, init_repo, preserved


def build(project):
    """Create the project and return the prompt, without the skill invocation."""
    init_repo(project)
    copy_fixture(FIXTURE, project)
    commit_all(project, 'Initial project')
    return 'TODO: the request the agent receives.'


def grade(project, evidence):
    """Map each assertion in case.toml to (passed, note)."""
    return {
        'behavior': (False, 'TODO: check the behavior'),
        'preserved': preserved(evidence),
    }
'''

# A runnable first case: it passes when the host loads the skill and changes nothing.
EXAMPLE_PY = '''"""Smoke case: the host loads the skill and leaves the project unchanged.

Replace the prompt and the checks with the behavior your skill promises.
"""
from seatrial.checks import commit_all, copy_fixture, init_repo, preserved, skill_loaded


def build(project):
    """Create the project and return the prompt, without the skill invocation."""
    init_repo(project)
    copy_fixture(FIXTURE, project)
    commit_all(project, 'Initial project')
    return 'Describe what this project does in one sentence. Do not change any file.'


def grade(project, evidence):
    """Map each assertion in case.toml to (passed, note)."""
    return {
        'skill-loaded': skill_loaded(evidence, CASE.skill),
        'preserved': preserved(evidence),
    }
'''

FIXTURE_README = '''# Tally

Tally counts words in text files and prints the ten most common words.
'''
