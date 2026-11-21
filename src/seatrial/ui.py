"""Terminal output: color, symbols, tables, progress, and prompts.

Color and arrow-key menus turn on only for an interactive terminal. NO_COLOR,
--no-color, a pipe, or TERM=dumb turn them off; prompts then read plain lines,
so scripts and agents can drive every prompt through stdin.
"""
import os
import re
import shutil
import sys

STYLES = {'bold': '1', 'dim': '2', 'red': '31', 'green': '32', 'yellow': '33', 'blue': '34',
          'magenta': '35', 'cyan': '36', 'gray': '90'}
STATUS_STYLE = {'completed': 'green', 'control': 'cyan', 'usage_limit': 'red', 'auth_error': 'red',
                'harness_error': 'red', 'timeout': 'yellow', 'host_stall': 'yellow', 'incomplete': 'yellow',
                'trace_incomplete': 'yellow', 'stale': 'yellow'}
VERDICT_STYLE = {'improved': 'green', 'non-inferior': 'green', 'regressed': 'red', 'inconclusive': 'yellow'}
ANSI = re.compile(r'\x1b\[[0-9;]*m')
_color = None


def set_color(enabled):
    global _color
    _color = enabled


def color_enabled(stream=None):
    if _color is not None:
        return _color
    stream = stream or sys.stdout
    return (stream.isatty() and 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb')


def interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def style(text, *names):
    if not color_enabled() or not names:
        return str(text)
    codes = ';'.join(STYLES[n] for n in names if n in STYLES)
    return f'\x1b[{codes}m{text}\x1b[0m'


def width(text):
    return len(ANSI.sub('', str(text)))


def count(number, noun):
    return f"{number} {noun}{'' if number == 1 else 's'}"


def ok(text):
    return f"{style('✓', 'green')} {text}"


def bad(text):
    return f"{style('✗', 'red')} {text}"


def warn(text):
    return f"{style('!', 'yellow')} {text}"


def info(text):
    return f"{style('›', 'cyan')} {text}"


def banner(title, subtitle=''):
    line = style(f' {title} ', 'bold', 'magenta')
    return line + (f"  {style(subtitle, 'dim')}" if subtitle else '')


def rule(char='─'):
    return style(char * min(72, shutil.get_terminal_size((80, 20)).columns), 'gray')


def table(headers, rows, max_width=None):
    """Align rows in columns. Cells may hold color codes; the last column wraps by truncation."""
    max_width = max_width or shutil.get_terminal_size((120, 20)).columns
    cells = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(width(row[i]) for row in cells) for i in range(len(headers))]
    room = max_width - sum(widths[:-1]) - 2 * (len(widths) - 1)
    if widths and room > 10 and widths[-1] > room:
        widths[-1] = room
    lines = []
    for index, row in enumerate(cells):
        parts = []
        for i, cell in enumerate(row):
            if i == len(row) - 1 and width(cell) > widths[i]:
                cell = ANSI.sub('', cell)[:widths[i] - 1] + '…'
            parts.append(cell + ' ' * (widths[i] - width(cell)))
        text = '  '.join(parts).rstrip()
        lines.append(style(text, 'bold') if index == 0 else text)
        if index == 0:
            lines.append(style('  '.join('─' * w for w in widths), 'gray'))
    return '\n'.join(lines)


def status(text):
    return style(text, STATUS_STYLE.get(text, 'yellow'))


def verdict(text):
    return style(text, VERDICT_STYLE.get(text, 'gray'))


def bar(done, total, size=24):
    filled = int(size * done / total) if total else size
    return style('█' * filled, 'green') + style('░' * (size - filled), 'gray')


def trial_line(record, finished, scheduled):
    """One progress line for a finished trial."""
    passed = record.get('pass')
    mark = style('✓', 'green') if passed else style('✗', 'red') if passed is False else style('•', 'yellow')
    cost = record.get('cost_usd')
    extras = [f"{record['wall_s']}s" if record.get('wall_s') is not None else None,
              f'${cost:.3f}' if isinstance(cost, (int, float)) else None]
    return (f"{bar(finished, scheduled)} {finished:>3}/{scheduled} {mark} {record['case_id']} "
            f"{style(record['host'], 'cyan')} {record['arm']} #{record['trial_index']} {status(record['status'])} "
            + style(' '.join(e for e in extras if e), 'dim'))


# Prompts. Each reads a plain line when no terminal is attached.

def ask(question, default=None, validate=None):
    hint = f" {style(f'[{default}]', 'dim')}" if default not in (None, '') else ''
    while True:
        try:
            answer = input(f"{style('?', 'magenta')} {question}{hint} ").strip()
        except EOFError:
            answer = ''
        answer = answer or (str(default) if default is not None else '')
        problem = validate(answer) if validate else None
        if not problem:
            return answer
        print(bad(problem))
        if not sys.stdin.isatty():
            raise SystemExit(2)


def confirm(question, default=True):
    answer = ask(question + style(' (Y/n)' if default else ' (y/N)', 'dim'), '').lower()
    return default if not answer else answer.startswith('y')


def choose(question, options, default=0):
    """Pick one option; options are (value, label) pairs. Arrow keys on a terminal, numbers elsewhere."""
    if interactive() and color_enabled():
        picked = _arrow_menu(question, options, default)
        if picked is not None:
            return picked
    print(f"{style('?', 'magenta')} {question}")
    for index, (_, label) in enumerate(options, 1):
        print(f"  {style(str(index), 'cyan')}. {label}")
    valid = lambda a: None if a.isdigit() and 1 <= int(a) <= len(options) else f'enter 1-{len(options)}'
    return options[int(ask('Choose', default + 1, valid)) - 1][0]


def choose_many(question, options, preselected=()):
    """Pick several options by number, comma separated; empty keeps the preselection, 'all' picks all."""
    print(f"{style('?', 'magenta')} {question}")
    for index, (value, label) in enumerate(options, 1):
        mark = style('●', 'green') if value in preselected else style('○', 'gray')
        print(f"  {mark} {style(str(index), 'cyan')}. {label}")

    def parse(answer):
        if answer in ('', 'all'):
            return None
        parts = [p.strip() for p in answer.split(',') if p.strip()]
        if all(p.isdigit() and 1 <= int(p) <= len(options) for p in parts):
            return None
        return f'enter numbers 1-{len(options)} separated by commas, or all'
    answer = ask('Numbers (comma separated, "all")', ','.join(
        str(i) for i, (v, _) in enumerate(options, 1) if v in preselected) or 'all', parse)
    if answer == 'all':
        return [v for v, _ in options]
    return [options[int(p) - 1][0] for p in answer.split(',') if p.strip()]


def _arrow_menu(question, options, default):
    try:
        import termios
        import tty
    except ImportError:
        return None
    index, fd = default, sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        return None
    out = sys.stdout

    def draw(first=False):
        if not first:
            out.write(f'\x1b[{len(options)}A')
        for i, (_, label) in enumerate(options):
            pointer = style('❯', 'magenta') if i == index else ' '
            text = style(label, 'bold') if i == index else label
            out.write(f'\r\x1b[2K {pointer} {text}\n')
        out.flush()

    out.write(f"{style('?', 'magenta')} {question} {style('(↑/↓, enter)', 'dim')}\n")
    draw(first=True)
    try:
        tty.setcbreak(fd)
        while True:
            key = sys.stdin.read(1)
            if key in ('\r', '\n'):
                return options[index][0]
            if key == '\x1b':
                sequence = sys.stdin.read(2)
                index = (index - 1) % len(options) if sequence == '[A' else (
                    (index + 1) % len(options) if sequence == '[B' else index)
            elif key in ('k', 'K'):
                index = (index - 1) % len(options)
            elif key in ('j', 'J'):
                index = (index + 1) % len(options)
            elif key.isdigit() and 1 <= int(key) <= len(options):
                index = int(key) - 1
            elif key in ('q', '\x03', '\x04'):
                raise KeyboardInterrupt
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
