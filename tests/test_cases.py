"""Case discovery, validation, selection, and the new-case scaffold."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from seatrial import cases, checks, cli, report
from seatrial.ports import new_evidence
from tests.support import ProjectCase

GOOD_TOML = '''id = "demo-001"
version = 1
skill = "demo"
suite = "capability"
summary = "A demo case."
primary = "behavior"
assertions = { behavior = "rate", preserved = "zero" }
'''
GOOD_PY = '''def build(project):
    project.mkdir(parents=True, exist_ok=True)
    return 'Say hello.'


def grade(project, evidence):
    return {'behavior': (True, 'ok'), 'preserved': (True, '')}
'''


def write_case(root, toml=GOOD_TOML, py=GOOD_PY, skill='demo', name='demo-001'):
    directory = Path(root) / skill / name
    directory.mkdir(parents=True)
    (directory / 'case.toml').write_text(toml)
    (directory / 'case.py').write_text(py)
    return directory


def empty_evidence():
    evidence = new_evidence()
    evidence.update(source_changes=[], git_changed=False, plugin_changed=False, project_base=None)
    return evidence


class DiscoveryTests(unittest.TestCase):
    def test_invalid_metadata_is_rejected_with_the_reason(self):
        bad = {
            'missing summary': GOOD_TOML.replace('summary = "A demo case."\n', ''),
            'unknown keys': GOOD_TOML + 'colour = "red"\n',
            'must match the folder': GOOD_TOML.replace('"demo-001"', '"other-001"'),
            'parent folder': GOOD_TOML.replace('skill = "demo"', 'skill = "other"'),
            'suite must be': GOOD_TOML.replace('"capability"', '"smoke"'),
            'primary must be': GOOD_TOML.replace('primary = "behavior"', 'primary = "missing"'),
            'assertion kinds': GOOD_TOML.replace('"rate"', '"often"'),
            'shared path': GOOD_TOML + 'shared = ["../../../outside.py"]\n',
            'trigger must be': GOOD_TOML + 'trigger = "sometimes"\n',
        }
        for reason, toml in bad.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                write_case(tmp, toml)
                with self.assertRaisesRegex(ValueError, reason):
                    cases.discover(tmp)

    def test_skill_triggered_agrees_with_skill_loaded_and_grades_by_default(self):
        evidence = empty_evidence()
        for skills, seen in ((['demo-plugin:demo'], True), (['/plugin/skills/demo/SKILL.md'], True),
                             (['.agents/skills/demo/SKILL.md'], True), (['/demo'], True), (['demo-plugin:other'], False),
                             ([], False)):
            with self.subTest(skills=skills):
                evidence['skills'] = skills
                self.assertEqual(checks.skill_triggered(evidence, 'demo')[0], seen)
                self.assertEqual(checks.skill_loaded(evidence, 'demo')[0], seen)
        evidence['skills'] = ['demo-plugin:demo-extra']
        self.assertFalse(checks.skill_triggered(evidence, 'demo')[0])
        with tempfile.TemporaryDirectory() as tmp:
            toml = GOOD_TOML.replace('{ behavior = "rate", preserved = "zero" }',
                                     '{ behavior = "rate", preserved = "zero", skill_triggered = "rate" }')
            write_case(tmp, toml + 'trigger = "auto"\n')
            case, = cases.discover(tmp).values()
            self.assertEqual(case.trigger, 'auto')
            evidence['skills'] = ['demo-plugin:demo']
            record = report.grade({}, case, Path(tmp), evidence)
            self.assertEqual([a['id'] for a in record['assertions'] if a['passed']],
                             ['behavior', 'preserved', 'skill_triggered'])

    def test_relative_skill_read_resolves_against_its_directory(self):
        evidence = empty_evidence()
        evidence['skills'] = ['SKILL.md']
        evidence['skill_reads'] = [{'path': 'SKILL.md', 'cwd': '.agents/skills/demo'}]
        self.assertEqual(checks.skill_triggered(evidence, 'demo'), (True, 'SKILL.md'))
        evidence['skill_reads'] = [{'path': 'SKILL.md', 'cwd': '.agents/skills/other'}]
        self.assertFalse(checks.skill_triggered(evidence, 'demo')[0])

    def test_shared_folders_are_not_cases_and_duplicates_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp)
            write_case(tmp, name='_shared')  # Holds helpers, never discovered as a case.
            self.assertEqual(list(cases.discover(tmp)), ['demo-001'])

    def test_build_must_not_include_the_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp, py=GOOD_PY.replace("'Say hello.'", "'/demo say hello.'"))
            case = cases.discover(tmp)['demo-001']
            with self.assertRaisesRegex(ValueError, 'skill invocation'):
                case.build(Path(tmp) / 'project')

    def test_select_filters_and_reports_unknown_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp)
            write_case(tmp, GOOD_TOML.replace('demo-001', 'demo-002').replace('"capability"', '"regression"')
                       .replace('summary', 'rules = ["R1"]\nsummary'), name='demo-002')
            found = cases.discover(tmp)
        self.assertEqual([c.id for c in cases.select(found, skills=['demo'])], ['demo-001', 'demo-002'])
        self.assertEqual([c.id for c in cases.select(found, suites=['regression'])], ['demo-002'])
        self.assertEqual([c.id for c in cases.select(found, rules=['R1'])], ['demo-002'])
        with self.assertRaisesRegex(ValueError, 'unknown case ids'):
            cases.select(found, ids=['missing-001'])

    def test_held_out_cases_run_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp)
            write_case(tmp, GOOD_TOML.replace('demo-001', 'demo-002') + 'holdout = true\n', name='demo-002')
            found = cases.discover(tmp)
        self.assertEqual([c.id for c in cases.select(found)], ['demo-001'])
        self.assertEqual([c.id for c in cases.select(found, holdout=True)], ['demo-002'])
        self.assertEqual([c.id for c in cases.select(found, holdout=None)], ['demo-001', 'demo-002'])
        with self.assertRaisesRegex(ValueError, 'held-out cases need --holdout: demo-002'):
            cases.select(found, ids=['demo-002'])
        with self.assertRaisesRegex(ValueError, '--holdout selects only held-out cases: demo-001'):
            cases.select(found, ids=['demo-001'], holdout=True)
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp, GOOD_TOML + 'holdout = "yes"\n')
            with self.assertRaisesRegex(ValueError, 'holdout must be a bool'):
                cases.discover(tmp)

    def test_identity_changes_with_grader_or_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_case(tmp)
            first = cases.parse(directory, tmp).identity()
            (directory / 'fixture').mkdir()
            (directory / 'fixture/README.md').write_text('a')
            second = cases.parse(directory, tmp).identity()
            (directory / 'case.py').write_text(GOOD_PY + '\n# changed\n')
            third = cases.parse(directory, tmp).identity()
        self.assertNotEqual(first['fixture_sha256'], second['fixture_sha256'])
        self.assertEqual(first['grader_sha256'], second['grader_sha256'])
        self.assertNotEqual(second['grader_sha256'], third['grader_sha256'])


class ScaffoldTests(ProjectCase, unittest.TestCase):
    def test_new_case_scaffold_is_discoverable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--config', str(self.cfg.file), 'new-case', '--skill', 'demo', '--id', 'demo-scope-001']), 0)
        case = cases.discover()['demo-scope-001']
        self.assertEqual(case.skill, 'demo')
        self.assertEqual(case.hosts, self.cfg.hosts)  # The configured default.
        project = self.root.parent / 'built'
        self.assertTrue(case.build(project).startswith('TODO'))
        self.assertTrue((project / 'README.md').is_file())

    def test_example_case_builds_and_grades(self):
        from seatrial import setup
        setup.new_case(self.cfg, 'demo', 'demo-smoke-001', example=True)
        case = cases.discover()['demo-smoke-001']
        project = self.root.parent / 'built'
        self.assertNotIn('/', case.build(project)[:1])
        evidence = empty_evidence()
        evidence['skills'] = ['plugin/skills/demo/SKILL.md']
        graded = case.grade(project, evidence)
        self.assertEqual(set(graded), {'skill-loaded', 'preserved'})


if __name__ == '__main__':
    unittest.main()
