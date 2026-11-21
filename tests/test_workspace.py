"""Skill mounts, manifests, and line counts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from seatrial import config, workspace
from seatrial.checks import git, init_repo, line_counts
from seatrial.workspace import WORKTREE, delta, manifest, mount
from tests.support import ProjectCase


def seeded(directory, files):
    project = Path(directory) / 'project'
    init_repo(project)
    for name, text in files.items():
        (project / name).write_text(text)
    git(project, 'add', '.')
    git(project, 'commit', '--quiet', '-m', 'feat: seed project')
    return project, git(project, 'rev-parse', 'HEAD')


class ManifestTests(unittest.TestCase):
    def test_runtime_artifacts_do_not_count_as_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.md').write_text('protected')
            before = manifest(root, source=True)
            (root / '.scratch').mkdir()
            (root / '.scratch/cache').write_text('native cache')
            (root / '__pycache__').mkdir()
            (root / '__pycache__/module.pyc').write_bytes(b'cache')
            self.assertEqual(before, manifest(root, source=True))
            (root / 'README.md').write_text('damaged')
            self.assertEqual(delta(before, manifest(root, source=True)), ['README.md'])

    def test_symlink_cannot_impersonate_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'README.md').write_text('same bytes')
            before = manifest(root, source=True)
            (root / 'target').write_text('same bytes')
            (root / 'README.md').unlink()
            (root / 'README.md').symlink_to(root / 'target')
            self.assertIn('README.md', delta(before, manifest(root, source=True)))

    def test_git_index_change_is_detected_separately_from_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
            (root / 'README.md').write_text('protected')
            source, metadata = manifest(root, source=True), manifest(root / '.git')
            subprocess.run(['git', 'add', 'README.md'], cwd=root, check=True)
            self.assertEqual(source, manifest(root, source=True))
            self.assertNotEqual(metadata, manifest(root / '.git'))


class LineCountTests(unittest.TestCase):
    def test_staged_unstaged_and_untracked_files_count_without_changing_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, base = seeded(tmp, {'tracked.txt': 'keep\nreplace\ndelete\n', 'removed.txt': 'gone\n',
                                         '.gitignore': 'ignored.txt\n'})
            self.assertEqual(line_counts(project, base), {'lines_added': 0, 'lines_removed': 0})
            (project / 'tracked.txt').write_text('keep\nreplaced\n')
            git(project, 'add', 'tracked.txt')
            (project / 'tracked.txt').write_text('keep\nreplaced\nmore\n')
            (project / 'removed.txt').unlink()
            (project / 'staged.txt').write_text('staged\n')
            git(project, 'add', 'staged.txt')
            (project / 'new\tname.txt').write_text('one\ntwo')
            (project / 'binary.dat').write_bytes(b'\0binary\n')
            (project / 'ignored.txt').write_text('ignored\n')
            (project / '.scratch').mkdir()
            (project / '.scratch/new.txt').write_text('excluded\n')
            before = manifest(project / '.git')
            self.assertEqual(line_counts(project, base), {'lines_added': 5, 'lines_removed': 3})
            self.assertEqual(manifest(project / '.git'), before)
            git(project, 'commit', '--quiet', '-m', 'feat: save part of the change')
            self.assertEqual(line_counts(project, base), {'lines_added': 5, 'lines_removed': 3})

    def test_cached_removal_measures_the_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, base = seeded(tmp, {'tracked.txt': 'one\ntwo\nthree\n'})
            git(project, 'rm', '--cached', '--quiet', 'tracked.txt')
            (project / 'tracked.txt').write_text('one\ntwo\nthree\nfour\n')
            self.assertEqual(line_counts(project, base), {'lines_added': 1, 'lines_removed': 0})

    def test_rename_with_edit_counts_only_edited_lines(self):
        original = ''.join(f'line {i}\n' for i in range(20))
        with tempfile.TemporaryDirectory() as tmp:
            project, base = seeded(tmp, {'old\tname.txt': original})
            (project / 'old\tname.txt').rename(project / 'new\nname.txt')
            (project / 'new\nname.txt').write_text(original.replace('line 10\n', 'changed\n'))
            (project / 'extra.txt').write_text('extra\n')
            self.assertEqual(line_counts(project, base), {'lines_added': 2, 'lines_removed': 1})


class MountTests(ProjectCase, unittest.TestCase):
    def test_mount_revision_extracts_the_whole_plugin(self):
        target = self.root.parent / 'p'
        identity = mount('HEAD', target)
        committed = workspace.git('ls-tree', '-r', '--name-only', 'HEAD', 'plugin').splitlines()
        self.assertEqual(set(manifest(target)), {p[len('plugin/'):] for p in committed})
        self.assertEqual(len(identity['sha']), 40)
        self.assertEqual(identity['tree'], workspace.git('rev-parse', 'HEAD:plugin'))

    def test_mount_worktree_includes_uncommitted_files(self):
        (self.cfg.skills / 'skills/demo/SKILL.md').write_text('changed\n')
        target = self.root.parent / 'p'
        identity = mount(WORKTREE, target)
        self.assertEqual(manifest(target), manifest(self.cfg.skills))
        self.assertTrue(identity['tree'].startswith('files:'))
        self.assertEqual((target / 'skills/demo/SKILL.md').read_text(), 'changed\n')
        self.assertTrue((target / 'references/contract.md').is_file())

    def test_run_directories_are_never_shared(self):
        first = workspace.new_run_directory('stamp')
        self.assertEqual(first.parent, self.cfg.state)
        with self.assertRaises(FileExistsError):
            workspace.new_run_directory('stamp')


def root_plugin(root):
    """A plugin at the repository root with the default evals/cases layout; activates its config."""
    init_repo(root)
    (root / '.claude-plugin').mkdir()
    (root / '.claude-plugin/plugin.json').write_text('{"name": "demo-plugin"}\n')
    (root / 'skills/demo').mkdir(parents=True)
    (root / 'skills/demo/SKILL.md').write_text('---\nname: demo\n---\n')
    (root / 'evals/cases/demo-001').mkdir(parents=True)
    (root / 'evals/cases/demo-001/case.py').write_text('# grader\n')
    (root / config.FILENAME).write_text(config.render(
        'demo', '.', 'plugin', 'demo-plugin', 'evals/cases', ['codex']))
    return root


def commit(root):
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', 'feat: seed project')


def graders(target):
    return [Path(d) / f for d, _, files in os.walk(target, followlinks=True) for f in files if f == 'case.py']


class RootPluginMountTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(config.deactivate)
        self.tmp = Path(tmp.name).resolve()
        self.root = root_plugin(self.tmp / 'project')

    def activate(self):
        config.activate(config.load(self.root / config.FILENAME))

    def test_root_plugin_mount_excludes_evaluation_inputs_and_git(self):
        commit(self.root)
        self.activate()
        for revision in (WORKTREE, 'HEAD'):
            with self.subTest(revision=revision):
                target = self.tmp / f'mount-{revision}'
                mount(revision, target)
                self.assertTrue((target / 'skills/demo/SKILL.md').is_file())
                self.assertTrue((target / '.claude-plugin/plugin.json').is_file())
                for name in ('evals', config.FILENAME, '.git'):
                    self.assertFalse((target / name).exists(), name)

    def test_links_to_evaluation_inputs_never_reach_the_mount(self):
        demo = self.root / 'skills/demo'
        (demo / 'fixtures').symlink_to('../../evals/cases')
        (demo / 'grader.py').symlink_to(self.root / 'evals/cases/demo-001/case.py')
        (demo / 'evals').symlink_to(self.root / 'evals')
        commit(self.root)
        self.activate()
        for revision in (WORKTREE, 'HEAD'):
            with self.subTest(revision=revision):
                target = self.tmp / f'mount-{revision}'
                mount(revision, target)
                self.assertTrue((target / 'skills/demo/SKILL.md').is_file())
                self.assertEqual(graders(target), [])
                for name in ('fixtures', 'grader.py', 'evals'):
                    self.assertFalse(os.path.lexists(target / 'skills/demo' / name), name)

    def test_git_cleanup_never_follows_a_linked_ancestor_outside_the_mount(self):
        outside = self.tmp / 'outside'
        (outside / 'cases').mkdir(parents=True)
        sentinel = outside / 'cases/keep.txt'
        sentinel.write_text('outside the mount\n')
        real = self.tmp / 'real-evals'
        (self.root / 'evals').rename(real)
        (self.root / 'evals').symlink_to(outside)  # HEAD records evals/ as a link out of the plugin.
        commit(self.root)
        (self.root / 'evals').unlink()
        real.rename(self.root / 'evals')  # The worktree has real cases at evals/cases again.
        self.activate()
        target = self.tmp / 'mount-HEAD'
        mount('HEAD', target)
        self.assertEqual(sentinel.read_text(), 'outside the mount\n')
        self.assertFalse(os.path.lexists(target / 'evals'))
        self.assertTrue((target / 'skills/demo/SKILL.md').is_file())


class SkillsLayoutMountTests(ProjectCase, unittest.TestCase):
    layout = 'skills'

    def test_skills_folder_mounts_as_a_generated_plugin(self):
        for revision in ('HEAD', WORKTREE):
            with self.subTest(revision=revision):
                target = self.root.parent / revision
                mount(revision, target)
                self.assertTrue((target / 'skills/demo/SKILL.md').is_file())
                manifest_file = json.loads((target / '.claude-plugin/plugin.json').read_text())
                self.assertEqual(manifest_file, {'name': 'skills', 'version': '0.0.0'})

    def test_worktree_runs_without_git(self):
        import shutil
        shutil.rmtree(self.root / '.git')
        identity = mount(WORKTREE, self.root.parent / 'p')
        self.assertIsNone(identity['sha'])
        self.assertTrue((self.root.parent / 'p/skills/other/SKILL.md').is_file())


if __name__ == '__main__':
    unittest.main()
