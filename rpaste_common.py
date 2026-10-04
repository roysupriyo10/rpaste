"""Shared limits, supported agents, and discovery of unwrapped system tools."""

import os
from pathlib import Path

MAX_IMAGE = 64 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SUPPORTED_AGENTS = frozenset(("claude", "codex"))


def find_executable(names, excluded=()):
    excluded = {Path(path).resolve() for path in excluded}
    for directory in os.get_exec_path():
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                if candidate.resolve() not in excluded:
                    return str(candidate)
    return None


def spawn_detached(command, log, pass_fds=()):
    """Start a background service without an abandoned Popen/wait lifecycle."""
    actions = [
        (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
        (os.POSIX_SPAWN_DUP2, log.fileno(), 1),
        (os.POSIX_SPAWN_DUP2, log.fileno(), 2),
    ]
    previous = {fd: os.get_inheritable(fd) for fd in pass_fds}
    try:
        for fd in pass_fds:
            os.set_inheritable(fd, True)
        try:
            return os.posix_spawnp(
                command[0], command, dict(os.environ), file_actions=actions, setsid=True
            )
        except NotImplementedError:
            # Some BSD libc builds lack POSIX_SPAWN_SETSID. A separate process
            # group still isolates the service from the caller's Ctrl+C.
            return os.posix_spawnp(
                command[0], command, dict(os.environ), file_actions=actions, setpgroup=0
            )
    finally:
        for fd, inheritable in previous.items():
            os.set_inheritable(fd, inheritable)
