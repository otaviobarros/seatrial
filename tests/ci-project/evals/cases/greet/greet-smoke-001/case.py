"""Smoke case: the host loads the skill and leaves the project unchanged.

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
