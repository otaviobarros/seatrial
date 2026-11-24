"""seatrial: run evaluation cases for agent skills on agent CLIs and compare revisions.

  seatrial                      interactive mode on a terminal
  seatrial init                 set up seatrial.toml and an example case
  seatrial doctor               check the configuration, cases, and host CLIs
  seatrial list [selectors]     list cases
  seatrial new-case --skill S --id NAME-001
  seatrial run [--rev REV] [selectors] [options]
  seatrial compare --base REV [--head REV] [selectors] [options]
  seatrial runs                 list retained run directories
  seatrial report [RUN_DIR|latest]
  seatrial regrade [RUN_DIR|latest]
  seatrial hosts                list host adapters and CLI versions
  seatrial schema               print the case, evidence, and exit-code contract

REV is a Git revision, WORKTREE (the default head) for the skill files in this
checkout, including uncommitted changes, or NONE for a baseline with no skill loaded
and the plain request. `run` and `compare` call host CLIs and spend
quota; use --dry-run to see the trial plan first. Add --json for machine-readable output.
"""
import argparse
import json
from pathlib import Path
import sys

from seatrial import __version__, config, report, runner, setup, ui, workspace
from seatrial import cases as case_index
from seatrial import hosts as registry
from seatrial.ports import EVIDENCE_KEYS, OPTIONAL_EVIDENCE_KEYS
from seatrial.workspace import NONE, WORKTREE

# Exit codes. Agents and CI can branch on them.
EXIT = {'ok': 0, 'failed': 1, 'usage': 2, 'config': 3, 'regressed': 4, 'no_cases': 5}
NEEDS_CONFIG = {'doctor': False, 'init': False, 'schema': False}


class Parser(argparse.ArgumentParser):
    """Usage errors become one JSON document on stdout when the command line has --json."""
    json_errors = False

    def error(self, message):
        if Parser.json_errors:
            fail(message, 'usage')
        super().error(message)


def report_error(message, code):
    """Print an error: JSON on stdout under --json, a marked line on stderr otherwise. Return its exit code."""
    if Parser.json_errors:
        print(json.dumps({'ok': False, 'error': message, 'exit': EXIT[code]}))
    else:
        print(ui.bad(message), file=sys.stderr)
    return EXIT[code]


def fail(message, code):
    sys.exit(report_error(message, code))


def add_selectors(parser):
    parser.add_argument('--case', action='append', default=[], help='Case ID; repeatable')
    parser.add_argument('--skill', action='append', default=[], help='Skill name; repeatable')
    parser.add_argument('--rule', action='append', default=[], help='Rule ID from case.toml; repeatable')
    parser.add_argument('--suite', action='append', default=[], choices=case_index.SUITES)
    parser.add_argument('--host', action='append', default=[], help='Host adapter; repeatable')
    parser.add_argument('--holdout', action='store_true',
                        help='Select only held-out cases; without it, run and compare skip them')
    parser.add_argument('--changed', metavar='REF',
                        help='Cases affected by files changed between REF and the working tree')


def add_run_options(parser):
    add_selectors(parser)
    parser.add_argument('--n', type=int, default=None, help='Trials per case, host, and arm (default from config)')
    parser.add_argument('--model', action='append', default=[], metavar='HOST=MODEL',
                        help='Model for one host; defaults to [hosts.models] or the adapter default')
    parser.add_argument('--effort', default=None, help='Override every case effort')
    parser.add_argument('--trigger', choices=case_index.TRIGGERS, default=None,
                        help='Override every case trigger: explicit invokes the skill, auto sends the request alone')
    parser.add_argument('--timeout', type=int, default=None, help='Seconds per trial; overrides case timeouts')
    parser.add_argument('--jobs', type=int, default=None, help='Parallel trials (default from config)')
    parser.add_argument('--max-runs', type=int, default=None, help='Refuse plans with more trials')
    parser.add_argument('--max-minutes', type=int, default=None, help='Stop scheduling after this')
    parser.add_argument('--max-cost-usd', type=float, default=None,
                        help='Stop scheduling once host-reported cost reaches this total')
    parser.add_argument('--budget-usd', type=float, default=None,
                        help='Per-trial spend cap, for hosts that support one (Claude)')
    parser.add_argument('--dry-run', action='store_true', help='Print the trial plan; make no native calls')
    parser.add_argument('--fail-on-regression', action='store_true',
                        help=f'Exit {EXIT["regressed"]} when a comparison verdict is "regressed"')
    add_json(parser)


def add_json(parser):
    parser.add_argument('--json', action='store_true', help='Print JSON to stdout; progress goes to stderr')


def build_parser():
    parser = Parser(prog='seatrial', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', action='version', version=f'seatrial {__version__}')
    parser.add_argument('--config', type=Path, default=None, help=f'Path to {config.FILENAME}')
    parser.add_argument('--no-color', action='store_true', help='Plain output without color')
    commands = parser.add_subparsers(dest='command')
    init = commands.add_parser('init', help='Set up seatrial.toml and an example case')
    init.add_argument('--skills', help='Folder with the skills (a plugin root or a folder of <name>/SKILL.md)')
    init.add_argument('--layout', choices=config.LAYOUTS)
    init.add_argument('--namespace', help='Claude plugin namespace (/<namespace>:<skill>)')
    init.add_argument('--cases', default='evals/cases', help='Case folder (default: evals/cases)')
    init.add_argument('--host', action='append', default=[], help='Host to enable; repeatable')
    init.add_argument('--name', help='Project name')
    init.add_argument('--no-example', action='store_true', help='Do not create the example case')
    init.add_argument('--force', action='store_true', help='Replace an existing seatrial.toml')
    init.add_argument('--yes', '-y', action='store_true', help='Accept detected values; never prompt')
    add_json(init)
    doctor = commands.add_parser('doctor', help='Check the configuration, cases, and host CLIs')
    doctor.add_argument('--no-probe', action='store_true', help='Skip host CLI version calls')
    add_json(doctor)
    listing = commands.add_parser('list', help='List cases')
    add_selectors(listing)
    add_json(listing)
    new = commands.add_parser('new-case', help='Create a case skeleton')
    new.add_argument('--skill')
    new.add_argument('--id')
    new.add_argument('--summary')
    add_json(new)
    single = commands.add_parser('run', help='Run cases against one skill revision')
    single.add_argument('--rev', default=WORKTREE, help='Skill revision, or NONE for no skill (default: WORKTREE)')
    add_run_options(single)
    pair = commands.add_parser('compare', help='Compare two skill revisions')
    pair.add_argument('--base', required=True, help='Revision before the change, for example main, or NONE for no skill')
    pair.add_argument('--head', default=WORKTREE, help='Revision after the change (default: WORKTREE)')
    add_run_options(pair)
    runs = commands.add_parser('runs', help='List retained run directories, newest first')
    runs.add_argument('--limit', type=int, default=20)
    add_json(runs)
    for name in ('report', 'regrade'):
        sub = commands.add_parser(name, help=f'{name.capitalize()} a retained run directory')
        sub.add_argument('run_dir', nargs='?', default='latest', help='Run directory, or latest (default)')
        if name == 'report':
            sub.add_argument('--format', choices=('table', 'markdown'), default='table')
        add_json(sub)
    hosts = commands.add_parser('hosts', help='List host adapters')
    hosts.add_argument('--no-probe', action='store_true', help='Skip host CLI version calls')
    add_json(hosts)
    schema = commands.add_parser('schema', help='Print the case, evidence, and exit-code contract')
    add_json(schema)
    commands.add_parser('interactive', help='Start interactive mode')
    return parser


def emit(args, data, text):
    print(json.dumps(data, indent=2, default=str) if getattr(args, 'json', False) else text)


def progress(args):
    """The per-trial callback: JSON lines on stderr with --json, a progress bar otherwise."""
    def show(record, finished, scheduled):
        if args.json:
            keys = ('case_id', 'host', 'arm', 'trial_index', 'status', 'pass', 'wall_s', 'cost_usd')
            print(json.dumps({'event': 'trial', 'finished': finished, 'scheduled': scheduled,
                              **{k: record.get(k) for k in keys}}), file=sys.stderr, flush=True)
        else:
            print(ui.trial_line(record, finished, scheduled), flush=True)
    return show


def selected(parser, args, holdout):
    """The selected cases. With --changed, also set args.because to {case id: reasons}."""
    args.because = None
    try:
        cases = case_index.discover()
        if args.changed is not None:
            top, paths = workspace.changed_files(args.changed)
            args.because = case_index.affected(cases, paths, top)
        found = case_index.select(cases, args.case, args.skill, args.rule, args.suite, args.host,
                                  holdout, args.because)
    except ValueError as error:
        parser.error(str(error))
    if not found and args.changed is not None and \
            case_index.select(cases, args.case, args.skill, args.rule, args.suite, args.host, holdout):
        fail('no affected cases', 'no_cases')
    if not found:
        parser.error('no case matches the selectors; run `seatrial list`')
    return found


def command_list(parser, args):
    try:
        empty = not case_index.discover()
    except ValueError as error:
        parser.error(str(error))
    if not any((args.case, args.skill, args.rule, args.suite, args.host, args.holdout, args.changed)) and empty:
        emit(args, [], ui.info('no cases yet; run `seatrial new-case`'))
        return EXIT['ok']
    # Listing shows held-out cases too, marked, so they are never forgotten.
    found = selected(parser, args, True if args.holdout else None)
    errors = {c.id: c.load_error() for c in found}
    because = args.because
    rows = [[c.id, c.skill, c.suite + (' holdout' if c.holdout else ''), ','.join(c.hosts),
             ui.style(errors[c.id], 'red') if errors[c.id] else c.summary]
            + (['; '.join(because[c.id])] if because is not None else []) for c in found]
    data = [{'id': c.id, 'skill': c.skill, 'suite': c.suite, 'holdout': c.holdout, 'hosts': c.hosts,
             'rules': c.rules,
             'summary': c.summary, 'assertions': c.assertions, 'primary': c.primary,
             'path': str(c.path), 'error': errors[c.id]}
            | ({'selected_because': because[c.id]} if because is not None else {}) for c in found]
    headers = ['Case', 'Skill', 'Suite', 'Hosts', 'Summary'] + (['Changed'] if because is not None else [])
    emit(args, data, ui.table(headers, rows) + '\n' + ui.style(ui.count(len(found), 'case'), 'dim'))
    return EXIT['ok']


def command_new_case(parser, args):
    cfg = config.current()
    skill, case_id = args.skill, args.id
    if (not skill or not case_id) and ui.interactive() and not args.json:
        names = setup.skill_names(cfg)
        if not skill:
            skill = ui.choose('Which skill does the case evaluate?', [(n, n) for n in names]) if names else \
                ui.ask('Skill name')
        if not case_id:
            existing = case_index.discover()
            number = 1
            while f'{skill}-{number:03d}' in existing:
                number += 1
            valid = lambda v: None if case_index.ID.match(v) and v not in existing else \
                'use name-001 and an unused id'
            case_id = ui.ask('Case id', f'{skill}-{number:03d}', valid)
        args.summary = args.summary or ui.ask('One sentence on the behavior it checks', '')
    if not skill or not case_id:
        parser.error('new-case needs --skill and --id (or a terminal for prompts)')
    try:
        written = setup.new_case(cfg, skill, case_id, args.summary or None)
    except ValueError as error:
        parser.error(str(error))
    note = '' if skill in setup.skill_names(cfg) else '\n' + ui.warn(f'{skill} is not in {cfg.skills}')
    emit(args, {'case': case_id, 'skill': skill, 'written': written},
         ui.ok(f'created {setup.relative(Path(written[0]).parent, cfg.root)}') +
         '\n' + ui.info('edit case.toml, case.py, and fixture/, then run `seatrial run --case ' +
                        case_id + ' --dry-run`') + note)
    return EXIT['ok']


def parse_models(parser, values, hosts):
    models = {h: registry.model(h) for h in hosts}
    for value in values:
        host, _, model = value.partition('=')
        if host not in hosts or not model:
            parser.error(f'--model {value!r}: use HOST=MODEL for one of {", ".join(hosts)}')
        models[host] = model
    return models


def resolve_options(parser, args):
    cfg, caps = config.current(), runner.limits()
    def pick(value, default):
        return default if value is None else value
    values = {'n': pick(args.n, cfg.defaults['n']), 'jobs': pick(args.jobs, cfg.defaults['jobs']),
              'max_runs': pick(args.max_runs, caps['max_runs']),
              'max_minutes': pick(args.max_minutes, caps['max_minutes'])}
    checks = [('n', values['n'], caps['max_n']), ('jobs', values['jobs'], caps['max_jobs']),
              ('max-runs', values['max_runs'], caps['max_runs']),
              ('max-minutes', values['max_minutes'], caps['max_minutes'])]
    if args.timeout is not None:
        checks.append(('timeout', args.timeout, caps['max_timeout']))
    for name, value, cap in checks:
        if not 1 <= value <= cap:
            parser.error(f'--{name} must be between 1 and {cap} (limits in {config.FILENAME})')
    return values


def plan(parser, args, revisions):
    """Validate a run request and return (cases, hosts, models, jobs, values)."""
    values = resolve_options(parser, args)
    for revision in revisions.values():
        try:
            workspace.check_revision(revision)
        except ValueError as error:
            parser.error(str(error))
    args.effort = args.effort or config.current().defaults['effort']
    chosen = selected(parser, args, args.holdout)
    broken = [error for error in (case.load_error() for case in chosen) if error]
    trigger = getattr(args, 'trigger', None)
    changed = [case.id for case in chosen if trigger and case.trigger != trigger]
    if changed:  # An explicit prompt names the skill, so skill_triggered no longer measures the description.
        print(ui.warn(f'--trigger {trigger} changes the mode of {", ".join(changed)}'), file=sys.stderr)
    if broken:
        parser.error('; '.join(broken))
    try:
        hosts = args.host or sorted({h for c in chosen for h in c.hosts})
        for host in hosts:
            adapter = registry.get(host)
            if args.effort and args.effort not in adapter.efforts:
                parser.error(f'{host} supports efforts {adapter.efforts}')
            if NONE in revisions.values() and not adapter.supports_no_plugin:
                parser.error(f'{host} does not support the NONE revision')
    except ValueError as error:
        parser.error(str(error))
    models = parse_models(parser, args.model, hosts)
    jobs = runner.schedule(chosen, hosts, list(revisions), values['n'])
    if not jobs:
        parser.error('no trial matches: the selected cases do not run on the selected hosts')
    if len(jobs) > values['max_runs']:
        parser.error(f'{len(jobs)} trials exceed --max-runs {values["max_runs"]}')
    return chosen, hosts, models, jobs, values


def plan_view(jobs, models, revisions, trigger=None):
    return [{'case': j['case'].id, 'host': j['host'], 'model': models[j['host']], 'arm': j['arm'],
             'revision': revisions[j['arm']], 'index': j['index'],
             'trigger': runner.effective_trigger(trigger or j['case'].trigger, revisions[j['arm']] == NONE)}
            for j in jobs]


def command_trials(parser, args, revisions):
    chosen, hosts, models, jobs, values = plan(parser, args, revisions)
    if args.dry_run:
        trials = plan_view(jobs, models, revisions, args.trigger)
        lines = [f"{t['case']} {t['host']}:{t['model']} {t['arm']}={t['revision']} #{t['index']}"
                 + (' trigger=auto' if t['trigger'] == 'auto' else '') for t in trials]
        data = {'dry_run': True, 'trials': trials, 'count': len(trials)}
        if args.because is not None:
            data['selected_because'] = {c.id: args.because[c.id] for c in chosen}
            lines = [ui.info(f"{c.id}: {'; '.join(args.because[c.id])}") for c in chosen] + lines
        emit(args, data, '\n'.join(lines + [ui.info(f'{len(jobs)} trials; no native calls made')]))
        return EXIT['ok']
    options = {'n': values['n'], 'models': models, 'effort': args.effort, 'timeout': args.timeout,
               'jobs': values['jobs'], 'max_minutes': values['max_minutes'], 'max_cost_usd': args.max_cost_usd,
               'budget_usd': args.budget_usd, 'trigger': args.trigger}
    if not args.json:
        print(ui.banner('seatrial', f'{len(jobs)} trials on {", ".join(hosts)}'))
    try:
        meta, records, work = runner.execute(chosen, hosts, revisions, options, on_record=progress(args))
    except SystemExit as error:
        return report_error(str(error), 'failed')
    return publish(args, meta, records, chosen, work)


def publish(args, meta, records, chosen, work, name='summary.json'):
    summary = report.summarize(meta, records, chosen)
    (work / name).write_text(json.dumps(summary, indent=2) + '\n')
    regressed = [f'{c} {h}' for c, e in summary['cases'].items() for h, r in e['hosts'].items()
                 if (r.get('decision') or {}).get('verdict') == 'regressed']
    if args.json:
        print(json.dumps({'run_dir': str(work), 'stopped_early': bool(meta.get('stopped_early')),
                          'regressed': regressed, 'summary': summary}, indent=2, default=str))
    else:
        print(report.terminal(summary))
        print(f'\nrun directory (local only): {work}')
    if meta.get('stopped_early'):
        return EXIT['failed']
    if regressed and getattr(args, 'fail_on_regression', False):
        return EXIT['regressed']
    return EXIT['ok']


def run_directory(parser, value):
    if value != 'latest':
        path = Path(value).expanduser()
        if not (path / 'records.jsonl').is_file():
            parser.error(f'{path} is not a run directory')
        return path
    runs = report.runs(config.current().state, config.current().root)
    if not runs:
        parser.error('no retained runs for this project yet; run `seatrial run`')
    return Path(runs[0]['path'])


def command_regrade(parser, args):
    work = run_directory(parser, args.run_dir)
    root = runner.run_meta(work).get('root')
    if root and root != str(config.current().root):
        parser.error(f'{work} belongs to the project at {root}; regrade it from there')
    try:
        cases = case_index.discover()
        meta, records = runner.regrade(work, cases)
    except ValueError as error:
        parser.error(str(error))
    chosen = [cases[c] for c in dict.fromkeys(r['case_id'] for r in records) if c in cases]
    return publish(args, meta, records, chosen, work, 'summary-regraded.json')


def command_report(parser, args):
    work = run_directory(parser, args.run_dir)
    path = work / 'summary-regraded.json'
    if not path.exists():
        path = work / 'summary.json'
    if not path.exists():
        parser.error(f'{work} has no summary; the run may still be going, or it stopped: run `seatrial regrade {work}`')
    summary = json.loads(path.read_text())
    text = report.markdown(summary) if args.format == 'markdown' else report.terminal(summary)
    emit(args, {'run_dir': str(work), 'summary': summary}, text)
    return EXIT['ok']


def command_runs(parser, args):
    found = report.runs(config.current().state, config.current().root)[:args.limit]
    rows = [[r['name'], r['finished'], ','.join(r['hosts']), len(r['cases']),
             '-' if r['cost_usd'] is None else f"{r['cost_usd']:.2f}", r['path']] for r in found]
    emit(args, found, ui.table(['Run', 'Trials', 'Hosts', 'Cases', 'Cost USD', 'Path'], rows)
         if found else ui.info('no retained runs yet'))
    return EXIT['ok']


def command_hosts(parser, args):
    data = []
    for name, adapter in sorted(registry.available().items()):
        version = None if args.no_probe else adapter.version()
        data.append({'name': name, 'default_model': adapter.default_model, 'model': registry.model(name),
                     'efforts': list(adapter.efforts), 'cli_version': version,
                     'enabled': name in config.current().hosts})
    rows = [[d['name'], d['model'], ','.join(d['efforts']),
             ui.style(d['cli_version'] or ('not probed' if args.no_probe else 'not found'),
                      'green' if d['cli_version'] else 'yellow'), 'yes' if d['enabled'] else 'no'] for d in data]
    emit(args, data, ui.table(['Host', 'Model', 'Efforts', 'CLI', 'Enabled'], rows))
    return EXIT['ok']


def command_schema(parser, args):
    from seatrial.ports import HostAdapter
    data = {
        'config_file': config.FILENAME,
        'config_sections': {k: sorted(v) for k, v in config.SECTIONS.items()},
        'case_layout': '<cases>/<skill>/<case-id>/{case.toml, case.py, fixture/}',
        'case_toml': {'required': list(case_index.REQUIRED),
                      'optional': sorted(set(case_index.Case.__dataclass_fields__) - set(case_index.REQUIRED)
                                         - {'path', 'module'}),
                      'suites': list(case_index.SUITES), 'assertion_kinds': list(case_index.KINDS),
                      'id_pattern': case_index.ID.pattern},
        'case_py': {'build(project)': 'create the project under `project`; return the prompt without the skill '
                                      'invocation',
                    'grade(project, evidence)': 'return {assertion: (passed, note)}; may return '
                                                '{"_status": "control", "_note": ...}',
                    'globals': ['CASE', 'ID', 'FIXTURE', 'SHARED']},
        'evidence_keys': {k: getattr(v, '__name__', str(v)) for k, v in EVIDENCE_KEYS.items()} | {
            k: f"{v.__name__} (optional)" for k, v in OPTIONAL_EVIDENCE_KEYS.items()} | {
            'project_base': 'str | None', 'source_changes': 'list', 'git_changed': 'bool',
            'plugin_changed': 'bool'},
        'statuses': {'graded': list(report.GRADED),
                     'invalid': ['incomplete', 'trace_incomplete', 'timeout', 'host_stall', 'usage_limit',
                                 'auth_error', 'harness_error', 'stale', 'control', 'contaminated']},
        'revisions': {'<git revision>': 'the skills folder at that commit',
                      WORKTREE: 'the skills folder in the checkout, uncommitted changes included',
                      NONE: 'no plugin and no skill invocation; the prompt is the request from build()'},
        'triggers': {'explicit': 'the prompt starts with the host skill invocation (default)',
                     'auto': 'the prompt is the request alone; the plugin stays mounted'},
        'built_in_assertions': {'skill_triggered': 'the trace shows the case skill; declare it under assertions to use it'},
        'verdicts': ['improved', 'non-inferior', 'regressed', 'inconclusive'],
        'host_adapter_methods': [m for m in ('version', 'invocation', 'prepare', 'command', 'environment',
                                             'collect', 'release', 'classify') if hasattr(HostAdapter, m)],
        'exit_codes': EXIT,
    }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            print(ui.style(key, 'bold'))
            print('  ' + json.dumps(value, indent=2).replace('\n', '\n  '))
    return EXIT['ok']


def command_init(parser, args):
    root = (args.config.parent if args.config else Path.cwd())
    prompt = ui.interactive() and not args.yes and not args.json
    if prompt:
        print(ui.banner('seatrial init', 'set up evaluations for this project'))
    try:
        result = setup.init(root, skills=args.skills, layout=args.layout, namespace=args.namespace,
                            cases=args.cases, hosts=args.host or None, name=args.name,
                            example=not args.no_example, force=args.force, prompt=prompt)
    except (config.ConfigError, ValueError) as error:
        return report_error(str(error), 'config')
    lines = [ui.ok(f"wrote {setup.relative(result['config'], root)}"),
             ui.info(f"skills: {setup.relative(result['skills'], root)} ({result['layout']}, "
                     f"{ui.count(len(result['skills_found']), 'skill')} found, /{result['namespace']}:<skill>)"),
             ui.info(f"cases: {setup.relative(result['cases'], root)}; hosts: {', '.join(result['hosts'])}")]
    lines += [ui.ok(f'created {setup.relative(p, root)}') for p in result['written'][1:]]
    lines += ['', ui.style('Next:', 'bold'), '  seatrial doctor', '  seatrial list',
              '  seatrial run --dry-run', '  seatrial run --host claude --n 1']
    emit(args, result, '\n'.join(lines))
    return EXIT['ok']


def command_doctor(parser, args):
    # An explicit --config is checked as given; never fall back to a configuration found here.
    cfg = None if args.config else config.active()
    results = setup.doctor(cfg, probe_hosts=not args.no_probe, path=args.config)
    if args.json:
        print(setup.doctor_json(results))
    else:
        print(setup.doctor_text(results))
    return EXIT['config'] if any(r['level'] == 'fail' for r in results) else EXIT['ok']


def load_config(parser, args):
    try:
        path = args.config or config.find()
        if path is None:
            raise config.ConfigError(f'no {config.FILENAME} found here or above; run `seatrial init`')
        config.activate(config.load(path))
    except config.ConfigError as error:
        if args.command != 'doctor':  # doctor reports the configuration as one of its checks.
            report_error(str(error), 'config')
        return False
    return True


def main(argv=None):
    parser = build_parser()
    Parser.json_errors = '--json' in (sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if args.no_color:
        ui.set_color(False)
    if args.command in (None, 'interactive'):
        if args.command is None and not ui.interactive():
            parser.print_help()
            return EXIT['usage']
        from seatrial import interactive
        return interactive.main(args.config)
    if args.command == 'doctor' and (args.config or config.find()):
        load_config(parser, args)
    elif NEEDS_CONFIG.get(args.command, True) and not load_config(parser, args):
        return EXIT['config']
    handlers = {'init': command_init, 'doctor': command_doctor, 'list': command_list,
                'new-case': command_new_case, 'runs': command_runs, 'report': command_report,
                'regrade': command_regrade, 'hosts': command_hosts, 'schema': command_schema}
    if args.command in handlers:
        return handlers[args.command](parser, args)
    if args.command == 'run':
        return command_trials(parser, args, {'run': args.rev})
    if args.base == args.head:
        parser.error('--base and --head must differ')
    return command_trials(parser, args, {'base': args.base, 'head': args.head})


def entry():
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n' + ui.warn('interrupted'), file=sys.stderr)
        sys.exit(130)


if __name__ == '__main__':
    entry()
