"""A configured hello-desk project for the seatrial skill's compare cases.

build() writes seatrial.toml with n = 3, one skill and one case per name, commits them, and
leaves an uncommitted edit to the greet skill that greet-001 measures, so no case is missing."""
from seatrial.checks import commit_all, init_repo

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

SKILLS = {
    'greet': ('Greets the user by name at the start of a conversation.',
              'Open with "Hello, NAME!" using the name the user gave.'),
    'farewell': ('Says goodbye by name when the user ends a conversation.',
                 'Close with "Goodbye, NAME!" using the name the user gave.'),
}

# Each skill's case: the request, and the pattern its final message must match.
REQUESTS = {
    'greet': ('Can you help me read notes.txt?', 'hello,? ana'),
    'farewell': ('Thanks for reading notes.txt with me, I am done for today.', 'goodbye,? ana'),
}

CASE_PY = '''from seatrial.checks import commit_all, init_repo, report_matches


def build(project):
    init_repo(project)
    (project / 'notes.txt').write_text('Visitor log\\\\n')
    commit_all(project, 'Initial project')
    return 'Hi, I am Ana. {request}'


def grade(project, evidence):
    return {{'named': report_matches(evidence, r'{pattern}')}}
'''


def skill_text(name):
    description, body = SKILLS[name]
    return f'---\nname: {name}\ndescription: {description}\n---\n\n# {name.title()}\n\n{body}\n'


def build(project, skills=('greet', 'farewell')):
    init_repo(project)
    (project / 'seatrial.toml').write_text(CONFIG)
    for name in skills:
        (project / 'skills' / name).mkdir(parents=True)
        (project / 'skills' / name / 'SKILL.md').write_text(skill_text(name))
        case = project / 'evals' / 'cases' / name / f'{name}-001'
        case.mkdir(parents=True)
        (case / 'case.toml').write_text(
            f'id = "{name}-001"\nversion = 1\nskill = "{name}"\nsuite = "capability"\n'
            f'summary = "{SKILLS[name][0]}"\nprimary = "named"\nassertions = {{ named = "rate" }}\n')
        request, pattern = REQUESTS[name]
        (case / 'case.py').write_text(CASE_PY.format(request=request, pattern=pattern))
    commit_all(project, 'Add the skills and their cases')
    (project / 'skills' / 'greet' / 'SKILL.md').write_text(
        skill_text('greet') + 'Greet with exactly "Hello, NAME!" before anything else.\n')
