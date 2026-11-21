"""Draw the README images into docs/images: python3 scripts/readme_images.py

The terminal capture comes from seatrial's own report and progress code, fed
with synthetic trial records, so it changes when that output changes.
"""
import random
import re
import sys
from html import escape
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/images'
sys.path.insert(0, str(ROOT / 'src'))

from seatrial import report, stats, ui  # noqa: E402

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

THEMES = {
    'light': {'text': '#0b2540', 'muted': '#4a6178', 'line': '#9fb3c8', 'panel': '#f3f7fb', 'stroke': '#c9d6e3',
              'accent': '#0b7a8f', 'accent_soft': '#d7f0f3', 'sea': '#1d6fa5', 'sea_soft': '#cfe5f5',
              'pass': '#1a7f37', 'fail': '#cf222e', 'warn': '#9a6700', 'chip_text': '#ffffff', 'hull': '#0b2540'},
    'dark': {'text': '#e6edf3', 'muted': '#9fb0c2', 'line': '#4b5d70', 'panel': '#161e29', 'stroke': '#2c3a4b',
             'accent': '#3cc6d8', 'accent_soft': '#123a44', 'sea': '#58a6ff', 'sea_soft': '#132b44',
             'pass': '#3fb950', 'fail': '#f85149', 'warn': '#d29922', 'chip_text': '#0d1117', 'hull': '#e6edf3'},
}


def svg(width, height, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" aria-label="{escape(label)}">\n{body}\n</svg>\n')


def text(x, y, content, size=14, fill='#000', weight=400, anchor='start', family=SANS, extra=''):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{escape(content)}</text>')


def buoy(cx, cy, passed, t):
    color = t['pass'] if passed else t['fail']
    mark = (f'<path d="M{cx - 5},{cy} l4,4 l7,-8" fill="none" stroke="{t["chip_text"]}" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"/>' if passed else
            f'<path d="M{cx - 4},{cy - 4} l8,8 M{cx + 4},{cy - 4} l-8,8" stroke="{t["chip_text"]}" '
            'stroke-width="2.4" stroke-linecap="round"/>')
    return f'<circle cx="{cx}" cy="{cy}" r="11" fill="{color}"/>{mark}'


def station(x, y, w, h, number, title, lines, t, code=None):
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{t["panel"]}" stroke="{t["stroke"]}"/>',
             f'<circle cx="{x + 26}" cy="{y + 28}" r="13" fill="{t["accent"]}"/>',
             text(x + 26, y + 33, str(number), 13, t['chip_text'], 700, 'middle'),
             text(x + 48, y + 33, title, 16, t['text'], 700)]
    for i, line in enumerate(lines):
        parts.append(text(x + 18, y + 66 + i * 19, line, 13, t['muted']))
    if code:
        parts.append(f'<rect x="{x + 14}" y="{y + h - 40}" width="{w - 28}" height="26" rx="6" '
                     f'fill="{t["accent_soft"]}"/>')
        parts.append(text(x + w / 2, y + h - 22, code, 12, t['accent'], 600, 'middle', MONO))
    return '\n'.join(parts)


def arrow(x1, y1, x2, y2, t):
    return (f'<path d="M{x1},{y1} L{x2 - 7},{y2}" stroke="{t["line"]}" stroke-width="2"/>'
            f'<path d="M{x2 - 9},{y2 - 6} L{x2},{y2} L{x2 - 9},{y2 + 6}" fill="none" stroke="{t["line"]}" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')


def how_it_works(t):
    w, h = 960, 330
    stations = [
        ('Case', ['A fictional project', 'and a plain request'], 'build()'),
        ('Mount', ['Your skills at one', 'revision, as a plugin'], 'main | WORKTREE'),
        ('Agent', ['The real CLI runs in', 'a fresh, isolated folder'], 'claude · codex'),
        ('Evidence', ['Commands, edits, skills', 'loaded, final message'], 'evidence.json'),
        ('Grade', ['Your code decides', 'pass or fail'], 'grade()'),
    ]
    bw, gap, top = 168, 20, 40
    parts = []
    for i, (title, lines, code) in enumerate(stations):
        x = 20 + i * (bw + gap)
        parts.append(station(x, top, bw, 170, i + 1, title, lines, t, code))
        if i:
            parts.append(arrow(x - gap + 3, top + 85, x - 3, top + 85, t))
    # Repeat bracket and the summary it feeds.
    parts.append(f'<path d="M20,236 v12 h920 v-12" fill="none" stroke="{t["line"]}" stroke-width="2"/>')
    parts.append(f'<path d="M480,248 v22" stroke="{t["line"]}" stroke-width="2"/>')
    parts.append(f'<rect x="250" y="270" width="460" height="44" rx="22" fill="{t["accent"]}"/>')
    parts.append(text(480, 297, 'Repeat n times per case, host, and revision  →  summary.json', 14,
                      t['chip_text'], 700, 'middle'))
    parts.append(text(20, 24, 'One trial', 13, t['muted'], 600, extra=' letter-spacing="1.2"'))
    return svg(w, h, '\n'.join(parts), 'How a trial runs: case, mount, agent, evidence, grade, repeated into a summary')


def compare(t):
    w, h = 960, 360
    base, head, n = 4, 10, 10
    p = stats.fisher(base, n, head, n)
    parts = [text(20, 30, 'seatrial compare --base main', 15, t['text'], 700, family=MONO),
             text(20, 54, 'Both revisions run the same case on the same host. Trials alternate, '
                  'so time of day and load hit both alike.', 13, t['muted'])]
    lanes = [('base', 'main', base, 110), ('head', 'WORKTREE', head, 190)]
    x0, step = 190, 46
    for arm, rev, passes, y in lanes:
        parts.append(f'<rect x="20" y="{y - 30}" width="630" height="60" rx="14" fill="{t["panel"]}" '
                     f'stroke="{t["stroke"]}"/>')
        parts.append(text(40, y - 3, arm, 15, t['text'], 700))
        parts.append(text(40, y + 16, rev, 12, t['accent'], 600, family=MONO))
        for i in range(n):
            offset = 0 if arm == 'base' else step / 2
            parts.append(buoy(x0 + i * step + offset, y, i < passes, t))
        parts.append(text(668, y + 6, f'{passes}/{n} pass', 16, t['pass'] if passes == n else t['text'], 700))
    parts.append(arrow(760, 150, 800, 150, t))
    parts.append(f'<rect x="806" y="104" width="138" height="92" rx="14" fill="{t["pass"]}"/>')
    parts.append(text(875, 140, 'improved', 19, t['chip_text'], 700, 'middle'))
    parts.append(text(875, 166, f'Fisher p = {p:.3f}', 13, t['chip_text'], 600, 'middle'))
    chips = [('regressed', t['fail'], 'a must-pass check newly fails, or a check got worse'),
             ('improved', t['pass'], 'the primary check is significantly better'),
             ('non-inferior', t['pass'], 'no check drops by more than two trials'),
             ('inconclusive', t['warn'], 'anything else, or too few valid trials')]
    for i, (name, color, meaning) in enumerate(chips):
        col, row = i % 2, i // 2
        x, y = 20 + col * 470, 262 + row * 44
        parts.append(f'<rect x="{x}" y="{y - 18}" width="112" height="28" rx="14" fill="{color}"/>')
        parts.append(text(x + 56, y + 1, name, 13, t['chip_text'], 700, 'middle'))
        parts.append(text(x + 124, y + 1, meaning, 13, t['muted']))
    return svg(w, h, '\n'.join(parts), f'compare: base passes {base} of {n}, head passes {head} of {n}, '
               'verdict improved')


TERM = {'30': '#6e7681', '31': '#ff7b72', '32': '#3fb950', '33': '#d29922', '34': '#58a6ff', '35': '#d2a8ff',
        '36': '#39c5cf', '90': '#6e7681'}


def capture():
    """Compare output for two cases on two hosts, from synthetic trial records."""
    ui.set_color(True)
    random.seed(7)
    cases = [SimpleNamespace(id='confirm-delete-001', skill='tidy-files', suite='capability', rules=[],
                             primary='asks', assertions={'asks': 'rate', 'no-delete': 'zero'},
                             identity=lambda: 'a'),
             SimpleNamespace(id='keep-tests-001', skill='tidy-files', suite='regression', rules=[],
                             primary='tests-kept', assertions={'tests-kept': 'zero'}, identity=lambda: 'b')]
    passes = {('confirm-delete-001', 'claude'): (4, 10), ('confirm-delete-001', 'codex'): (3, 9),
              ('keep-tests-001', 'claude'): (10, 10), ('keep-tests-001', 'codex'): (10, 10)}
    records = []
    for (case_id, host), counts in passes.items():
        case = next(c for c in cases if c.id == case_id)
        for arm, count in zip(('base', 'head'), counts):
            for i in range(10):
                checks = [{'id': a, 'passed': i < count if a == case.primary else True} for a in case.assertions]
                records.append({'case_id': case_id, 'host': host, 'arm': arm, 'trial_index': i + 1,
                                'status': 'completed', 'pass': all(c['passed'] for c in checks),
                                'assertions': checks, 'wall_s': round(random.uniform(28, 64), 1),
                                'cost_usd': round(random.uniform(0.02, 0.06), 3) if host == 'claude' else None,
                                'lines_added': 0})
    summary = report.summarize({'arms': ['base', 'head'], 'n': 10}, records, cases)
    order = sorted(records, key=lambda r: (r['trial_index'], r['case_id'], r['host'], r['arm'] != 'base'))
    lines = [ui.style('$ ', 'gray') + 'seatrial compare --base main --n 10 --max-cost-usd 5',
             ui.banner('seatrial', f'{len(records)} trials on claude, codex')]
    lines += [ui.trial_line(order[k], k + 1, len(records)) for k in range(3)]
    lines += [ui.style('  …', 'dim'), ui.trial_line(order[-1], len(records), len(records)), '']
    lines += report.terminal(summary).split('\n')
    return lines


def terminal_svg(lines):
    size, char, lead = 13, 7.83, 19
    cols = max(len(ui.ANSI.sub('', line)) for line in lines)
    w, top = int(cols * char) + 48, 52
    h = top + lead * len(lines) + 20
    body = [f'<rect width="{w}" height="{h}" rx="12" fill="#0d1117"/>',
            f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" fill="none" stroke="#30363d"/>',
            '<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/>'
            '<circle cx="62" cy="20" r="6" fill="#28c840"/>',
            text(w / 2, 25, 'seatrial compare', 12, '#8b949e', 600, 'middle')]
    for row, line in enumerate(lines):
        spans, state = [], {}
        for piece in re.split(r'(\x1b\[[0-9;]*m)', line):
            match = re.fullmatch(r'\x1b\[([0-9;]*)m', piece)
            if match:
                codes = match.group(1).split(';')
                state = {} if codes == ['0'] else {**state, **{('weight' if c == '1' else 'dim' if c == '2'
                                                                else 'color'): c for c in codes}}
                continue
            if not piece:
                continue
            fill = TERM.get(state.get('color'), '#c9d1d9')
            if state.get('dim'):
                fill = '#8b949e'
            weight = ' font-weight="700"' if state.get('weight') else ''
            spans.append(f'<tspan fill="{fill}"{weight}>{escape(piece)}</tspan>')
        body.append(f'<text x="24" y="{top + row * lead}" font-family="{MONO}" font-size="{size}" '
                    f'xml:space="preserve">{"".join(spans)}</text>')
    return svg(w, h, '\n'.join(body), 'Terminal output of seatrial compare with verdicts per case and host')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, draw in (('how-it-works', how_it_works), ('compare', compare)):
        for theme, palette in THEMES.items():
            (OUT / f'{name}-{theme}.svg').write_text(draw(palette))
    (OUT / 'terminal.svg').write_text(terminal_svg(capture()))


if __name__ == '__main__':
    main()
