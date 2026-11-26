"""Setup: a project with one skill and no seatrial.toml.

The skill should run `init`, write a case with `new-case`, check it without model
calls, and leave native runs for the user to approve.
"""
from seatrial.checks import commit_all, copy_fixture, init_repo, skill_loaded

cli = SHARED['cli']


def build(project):
    init_repo(project)
    copy_fixture(FIXTURE, project)
    commit_all(project, 'Add the greet skill')
    return ('I want to know if my greet skill actually works. Set this project up so I can '
            'measure it, and add one scenario that checks the agent greets the user by name. '
            'Do not spend any model calls yet; I will decide on a budget later.')


def grade(project, evidence):
    configured = (project / 'seatrial.toml').is_file()
    cases, note = cli.list_cases(project) if configured else (None, 'no seatrial.toml')
    greet = [c for c in cases or [] if c.get('skill') == 'greet']
    loads = [c for c in greet if c.get('error') is None]
    return {
        'skill-loaded': skill_loaded(evidence, CASE.skill),
        'configured': (configured, 'seatrial.toml written' if configured else 'no seatrial.toml'),
        'case-loads': (bool(loads), ', '.join(c['id'] for c in loads)
                       or note or '; '.join(f"{c['id']}: {c['error']}" for c in greet)[:300] or 'no greet case'),
        'no-native-run': cli.no_native_run(evidence),
    }
