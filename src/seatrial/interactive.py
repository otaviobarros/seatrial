"""Menu-driven terminal mode. Every action maps to a CLI command, which the menu prints first."""
import shlex

from seatrial import cli, config, setup, ui
from seatrial import cases as case_index
from seatrial.workspace import WORKTREE


BASE = []  # --config PATH once a configuration is loaded


def run(argv):
    print(ui.style('$ seatrial ' + shlex.join(argv), 'dim'))
    try:
        code = cli.main(BASE + argv)
    except SystemExit as error:  # argparse errors exit; stay in the menu.
        code = error.code
    print()
    return code


def pick_cases():
    found = case_index.discover()
    tuning = [c for c in found.values() if not c.holdout]
    held = len(found) - len(tuning)
    skills = sorted({c.skill for c in tuning})
    scopes = [('all', f'All cases ({len(tuning)})'), ('skill', 'One skill'), ('case', 'One case')] if tuning else []
    if held:
        scopes.append(('holdout', f'Held-out cases ({held}); run once, after the edit is final'))
    scope = ui.choose('Which cases?', scopes)
    if scope == 'holdout':
        return ['--holdout']
    if scope == 'skill':
        return ['--skill', ui.choose('Skill', [(s, s) for s in skills])]
    if scope == 'case':
        return ['--case', ui.choose('Case', [(c.id, f'{c.id}  {ui.style(c.summary, "dim")}') for c in tuning])]
    return []


def pick_hosts(cfg):
    hosts = ui.choose_many('Which hosts?', [(h, h) for h in cfg.hosts], preselected=cfg.hosts)
    return [arg for h in hosts for arg in ('--host', h)]


def trials(command):
    cfg = config.current()
    argv = [command]
    if command == 'compare':
        argv += ['--base', ui.ask('Base revision (before the change; NONE for no skill)', 'main')]
        argv += ['--head', ui.ask('Head revision (after the change)', WORKTREE)]
    else:
        argv += ['--rev', ui.ask('Skill revision', WORKTREE)]
    argv += pick_cases() + pick_hosts(cfg)
    argv += ['--n', ui.ask('Trials per case, host, and arm', cfg.defaults['n'],
                           lambda v: None if v.isdigit() and 1 <= int(v) <= cfg.limits['max_n']
                           else f"enter 1-{cfg.limits['max_n']}")]
    if run(argv + ['--dry-run']) != cli.EXIT['ok']:
        return
    if ui.confirm('Run these trials now? This calls the host CLIs and spends quota.', False):
        run(argv)


def browse_runs():
    found = cli.report.runs(config.current().state)
    if not found:
        print(ui.info('no retained runs yet') + '\n')
        return
    work = ui.choose('Which run?', [(r['path'], f"{r['name']}  {ui.style(', '.join(r['cases'])[:60], 'dim')}")
                                    for r in found[:15]])
    action = ui.choose('Action', [('report', 'Show the report'), ('markdown', 'Show the Markdown report'),
                                  ('regrade', 'Regrade with the current case code')])
    if action == 'markdown':
        run(['report', work, '--format', 'markdown'])
    else:
        run([action, work])


def main(config_path=None):
    print(ui.banner('seatrial', 'evaluate agent skills') + '\n')
    path = config_path or config.find()
    if path is None:
        print(ui.warn(f'no {config.FILENAME} found here or above.'))
        if not ui.confirm('Set up this folder now?', True):
            return cli.EXIT['config']
        if run(['init']) != cli.EXIT['ok']:
            return cli.EXIT['config']
        path = config.find()
    try:
        cfg = config.activate(config.load(path))
    except config.ConfigError as error:
        print(ui.bad(str(error)))
        return cli.EXIT['config']
    BASE[:] = ['--config', str(path)]
    actions = [('list', 'List cases'), ('new', 'Create a case'), ('run', 'Run cases (dry run first)'),
               ('compare', 'Compare two revisions'), ('runs', 'Browse past runs'),
               ('doctor', 'Check the setup'), ('hosts', 'Show host CLIs'), ('quit', 'Quit')]
    while True:
        try:
            cases = ui.count(len(case_index.discover()), 'case')
        except ValueError as error:
            cases = 'cases unreadable'
            print(ui.bad(str(error)))
        print(ui.info(f"{cfg.name}: {ui.count(len(setup.skill_names(cfg)), 'skill')}, "
                      f"{cases}, hosts {', '.join(cfg.hosts)}"))
        try:
            action = ui.choose('What next?', actions)
        except KeyboardInterrupt:
            print()
            return cli.EXIT['ok']
        if action == 'quit':
            return cli.EXIT['ok']
        try:
            if action == 'list':
                run(['list'])
            elif action == 'new':
                run(['new-case'])
            elif action in ('run', 'compare'):
                trials(action)
            elif action == 'runs':
                browse_runs()
            else:
                run([action])
        except KeyboardInterrupt:
            print('\n' + ui.warn('cancelled') + '\n')
        except ValueError as error:  # An unreadable case.toml; the message names the file.
            print(ui.bad(str(error)) + '\n')
