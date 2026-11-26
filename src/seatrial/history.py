"""Results across runs: one history row per case, host, and arm, and the trend series built from them.

Each finished `run` or `compare` appends its rows to `<state>/history.jsonl`. Projects
can share a state directory, so every row names its project root and `trend` reads only
the current project's rows. A row is a summary of one arm, not its trials; the run
directory, while it is retained, holds the trials.
"""
import datetime
import json
from pathlib import Path

from seatrial import config, stats

FILENAME = 'history.jsonl'
SCHEMA = 1
# Identity fields whose change `trend` marks, in the order the markers print.
MARKED = (('skill_tree', 'skill'), ('grader_sha256', 'case'), ('fixture_sha256', 'fixture'), ('model', 'model'),
          ('cli_version', 'cli'))
FORMATS = ('table', 'markdown')
# Fields trend needs; a row without them is counted as unreadable.
REQUIRED = ('date', 'root', 'case_id', 'host', 'arm', 'valid', 'primary_passes', 'all_pass')


def path():
    """The history file of the active configuration."""
    return Path(config.current().state) / FILENAME


def _first(records, key):
    """The value most trials of a group report; trials of one arm share it unless a run was edited."""
    values = [r.get(key) for r in records if r.get(key) is not None]
    return max(values, key=values.count) if values else None  # A tie keeps the first trial's value.


def rows(summary, records, work, command, date=None):
    """The history rows of one finished run: one per case, host, and arm in its summary."""
    date = date or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    trees = summary.get('skill_trees') or {}
    found = []
    for case_id, entry in summary['cases'].items():
        for host, result in entry['hosts'].items():
            verdict = (result.get('decision') or {}).get('verdict')
            for arm, s in result['arms'].items():
                group = [r for r in records if r['case_id'] == case_id and r['host'] == host and r['arm'] == arm]
                revision = (summary.get('revisions') or {}).get(arm) or {}
                primary = entry['primary']
                found.append({
                    'schema': SCHEMA, 'date': date, 'root': summary.get('root'), 'project': summary.get('project'),
                    'command': command, 'run_dir': str(work), 'stopped_early': bool(summary.get('stopped_early')),
                    'case_id': case_id, 'skill': entry['skill'], 'case_version': entry['identity']['version'],
                    'grader_sha256': entry['identity']['grader_sha256'],
                    'fixture_sha256': entry['identity'].get('fixture_sha256'), 'host': host, 'arm': arm,
                    'revision': {k: revision.get(k) for k in ('name', 'sha', 'dirty') if k in revision},
                    'skill_tree': (trees.get(arm) or {}).get(entry['skill']),
                    'model': _first(group, 'model') or (summary.get('models') or {}).get(host),
                    'cli_version': _first(group, 'cli_version') or (summary.get('cli_versions') or {}).get(host),
                    'effort': _first(group, 'effort'), 'trigger': _first(group, 'trigger'),
                    'trials': s['trials'], 'valid': s['valid'], 'primary': primary,
                    'primary_passes': s['assertion_passes'][primary], 'primary_ci': s['primary_ci'],
                    'assertion_passes': s['assertion_passes'], 'all_pass': s['all_pass'],
                    'cost_usd_total': s.get('cost_usd_total'), 'cost_usd_median': s.get('cost_usd_median'),
                    'label': summary.get('label'),  # 'a/a' marks a noise-floor run: its verdict measures noise.
                    'verdict': verdict if arm == 'head' else None})  # The verdict judges head against base.
    return found


def append(found):
    """Append rows in one write, so concurrent runs never interleave inside a line."""
    if not found:
        return None
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('a', encoding='utf-8') as handle:
        handle.write(''.join(json.dumps(row, sort_keys=True) + '\n' for row in found))
    return target


def parse_since(value):
    """A date or ISO date-time as an aware datetime; a value without a zone is UTC."""
    moment = datetime.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def read(root=None, cases=(), skills=(), hosts=(), arms=()):
    """The current project's rows, oldest first, filtered; unreadable lines are skipped and counted.

    Return (rows, skipped)."""
    root = str(root or config.current().root)
    target = path()
    if not target.is_file():
        return [], 0
    found, skipped = [], 0
    for line in target.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            parse_since(row['date'])
            if any(key not in row for key in REQUIRED):
                raise KeyError('missing fields')
        except (ValueError, KeyError, TypeError, AttributeError):
            skipped += 1
            continue
        if row.get('root') != root or (cases and row.get('case_id') not in cases) or \
                (skills and row.get('skill') not in skills) or (hosts and row.get('host') not in hosts) or \
                (arms and row.get('arm') not in arms):
            continue
        found.append(row)
    found.sort(key=lambda row: parse_since(row['date']))  # Stable: rows of one run keep their order.
    return found, skipped


def series(found):
    """Rows grouped by case, host, and arm, each with `changes`: the marked fields that differ from the
    previous row of its series. The first row of a series has none."""
    grouped = {}
    for row in found:
        grouped.setdefault((row['case_id'], row['host'], row['arm']), []).append(row)
    for group in grouped.values():
        previous = None
        for row in group:
            row['changes'] = [] if previous is None else [
                {'field': key, 'label': label, 'from': previous.get(key), 'to': row.get(key)}
                for key, label in MARKED if previous.get(key) != row.get(key)]
            previous = row
    return grouped


def _short(value, key):
    if value is None:
        return 'none'
    return value[:8] if key in ('skill_tree', 'grader_sha256', 'fixture_sha256') else str(value)


def change_text(row):
    """The markers of one row, for example `skill a1b2c3d4→e5f6a7b8; model m1→m2`, or ''."""
    return '; '.join(f"{c['label']} {_short(c['from'], c['field'])}→{_short(c['to'], c['field'])}"
                     for c in row.get('changes') or [])


def _cells(row):
    rate = stats.percent({'passes': row['primary_passes'], 'valid': row['valid'], 'ci': row.get('primary_ci')})
    revision = row.get('revision') or {}
    name = revision.get('name') or '-'
    if revision.get('sha') and name != revision['sha']:
        name += f" ({revision['sha'][:7]}{'+' if revision.get('dirty') else ''})"
    cost = row.get('cost_usd_median')
    return [row['date'][:16].replace('T', ' '), name, row.get('model') or '-', rate,
            f"{row['all_pass']}/{row['valid']}", '-' if cost is None else f'{cost:.4f}',
            ('A/A ' if row.get('label') == 'a/a' and row.get('verdict') else '') + (row.get('verdict') or '-')
            + (', partial' if row.get('stopped_early') else ''), change_text(row)]


HEADERS = ['Date (UTC)', 'Revision', 'Model', 'Primary pass (95% CI)', 'All pass', 'Cost USD median', 'Verdict',
           'Changed']


def markdown(grouped):
    """The series as Markdown: one heading and table per case, host, and arm."""
    if not grouped:
        return 'No trend history for this project yet.'
    lines = []
    for (case_id, host, arm), group in grouped.items():
        lines += [f'#### `{case_id}` on {host}, {arm}', '', '| ' + ' | '.join(HEADERS) + ' |',
                  '|' + '---|' * len(HEADERS)]
        for row in group:
            lines.append('| ' + ' | '.join(cell.replace('|', '\\|') for cell in _cells(row)) + ' |')
        lines.append('')
    return '\n'.join(lines).rstrip()


def terminal(grouped):
    """The series as aligned tables for a terminal, with changed rows marked."""
    from seatrial import ui
    if not grouped:
        return ui.info('no trend history for this project yet; finished `run` and `compare` commands add it')
    blocks = []
    for (case_id, host, arm), group in grouped.items():
        rows = []
        for row in group:
            cells = _cells(row)
            if cells[-1]:
                cells[-1] = ui.style(cells[-1], 'yellow')
            rows.append(cells)
        blocks.append(ui.style(f'{case_id} {host} {arm}', 'bold') + '\n' + ui.table(HEADERS, rows))
    return '\n\n'.join(blocks)


def render(grouped, fmt):
    return markdown(grouped) if fmt == 'markdown' else terminal(grouped)
