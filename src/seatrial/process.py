"""Bounded process launch. Only process exit or the time cap ends a trial."""
import os
from pathlib import Path
import signal
import subprocess
import time

# A clock that jumps by more than this between wait slices means the whole host paused.
STALL_GAP, WAIT_SLICE, KILL_GRACE = 10.0, 0.5, 5.0


def _parents():
    """Map parent pid to child pids, from /proc on Linux or ps elsewhere."""
    children = {}
    proc = Path('/proc')
    if proc.is_dir() and any(proc.glob('[0-9]*/stat')):
        for stat in proc.glob('[0-9]*/stat'):
            try:
                fields = stat.read_text().rsplit(')', 1)[1].split()
            except (OSError, IndexError):
                continue
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        return children
    try:
        listing = subprocess.run(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True, text=True,
                                 timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return children
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    return children


def descendants(root):
    """Live descendants of root, including ones that moved to another session
    (sandboxed tool commands can leave the process group)."""
    children, found, queue = _parents(), [], [root]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def _signal(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        result = os.waitpid(pid, os.WNOHANG)  # Reap our own zombies; others raise.
        return result == (0, 0)
    except ChildProcessError:
        pass
    stat = Path(f'/proc/{pid}/stat')
    if stat.exists():
        try:
            return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except (OSError, IndexError):
            return False
    try:
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(state) and not state.startswith('Z')


def launch(command, cwd, env, stdout, stderr, timeout):
    """Run command to exit or to the time cap, then stop its whole process tree.

    The cap is enforced on the monotonic clock in short slices. A slice in which either
    clock jumped by more than STALL_GAP means this process was not running (host pause);
    that time is reported as stalled_s so a stall is not mistaken for a slow run."""
    started, clock = time.time(), time.monotonic()
    deadline, stalled = clock + timeout, 0.0
    with open(stdout, 'w') as out, open(stderr, 'w') as err:
        # Native CLIs read stdin when it is open; never inherit an interactive one.
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        last_wall, last_clock = started, clock
        while True:
            try:
                code = process.wait(timeout=max(0.0, min(WAIT_SLICE, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                code = None
            now_wall, now_clock = time.time(), time.monotonic()
            gap = max(now_wall - last_wall, now_clock - last_clock)
            if gap > STALL_GAP:
                stalled += gap - WAIT_SLICE
            last_wall, last_clock = now_wall, now_clock
            if code is not None:
                return {'exit': code, 'started': started, 'ended': now_wall, 'stalled_s': round(stalled, 1)}
            if now_clock >= deadline:
                break
        tree = descendants(process.pid)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            _signal(tree, sig)
            try:
                process.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                pass
            tree += [pid for pid in descendants(process.pid) if pid not in tree]
        give_up = time.monotonic() + KILL_GRACE
        while any(_alive(pid) for pid in tree) and time.monotonic() < give_up:
            time.sleep(0.05)
        survivors = [pid for pid in tree if _alive(pid)] + ([process.pid] if process.poll() is None else [])
        result = {'timeout': timeout, 'started': started, 'ended': time.time(), 'stalled_s': round(stalled, 1)}
        if survivors:
            result['kill_failed'] = survivors
        return result
