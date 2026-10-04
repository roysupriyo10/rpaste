#!/usr/bin/env python3
"""A private, authenticated X clipboard for agents running over SSH."""

import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys

from rpaste_common import find_executable, spawn_detached


def real_xclip():
    bridge = Path(__file__).with_name("rpaste").resolve()
    binary = find_executable(("xclip.real", "xclip"), (bridge,))
    if binary:
        return binary
    raise RuntimeError("xclip is missing; run dotfiles/install.sh")


def clipboard_env(state):
    root = Path(state) / "clipboard"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with (root / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        metadata = root / "display.json"
        try:
            server = json.loads(metadata.read_text())
            os.kill(server["pid"], 0)
            env = os.environ.copy()
            env.update(DISPLAY=server["display"], XAUTHORITY=server["authority"])
            # An authenticated X11 setup request also detects stale metadata
            # or a recycled PID/display, without requiring a clipboard owner.
            cookie = Path(server["authority"]).read_bytes()[-16:]
            name = b"MIT-MAGIC-COOKIE-1"
            request = struct.pack(
                "<BBHHHHH", ord("l"), 0, 11, 0, len(name), len(cookie), 0
            )
            request += name + b"\0" * (-len(name) % 4) + cookie
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(2)
                probe.connect(f"/tmp/.X11-unix/X{server['display'][1:]}")
                probe.sendall(request)
                healthy = probe.recv(1) == b"\x01"
            if healthy:
                return env
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass

        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise RuntimeError("Xvfb is missing; run dotfiles/install.sh")
        # FamilyWild matches the display chosen by -displayfd. Both Xvfb and
        # its clients read this MIT cookie; TCP access stays disabled.
        authority = root / f"authority-{secrets.token_hex(8)}"
        fields = (b"", b"", b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16))
        with authority.open("xb") as stream:
            os.chmod(authority, 0o600)
            stream.write(struct.pack("!H", 65535))
            for field in fields:
                stream.write(struct.pack("!H", len(field)) + field)
        read_fd, write_fd = os.pipe()
        try:
            with (root / "server.log").open("ab") as log:
                server_pid = spawn_detached(
                    [
                        xvfb,
                        "-displayfd",
                        str(write_fd),
                        "-screen",
                        "0",
                        "1x1x24",
                        "-nolisten",
                        "tcp",
                        "-auth",
                        str(authority),
                    ],
                    log,
                    pass_fds=(write_fd,),
                )
            os.close(write_fd)
            write_fd = -1
            if not select.select([read_fd], [], [], 8)[0]:
                os.kill(server_pid, signal.SIGKILL)
                os.waitpid(server_pid, 0)
                raise RuntimeError(
                    "private clipboard startup timed out (see clipboard/server.log)"
                )
            number = os.read(read_fd, 64).decode().strip()
            if not number.isdigit():
                os.waitpid(server_pid, 0)
                raise RuntimeError(
                    "private clipboard failed to start (see clipboard/server.log)"
                )
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)
        server = dict(pid=server_pid, display=f":{number}", authority=str(authority))
        temporary = metadata.with_suffix(".tmp")
        temporary.write_text(json.dumps(server))
        os.chmod(temporary, 0o600)
        temporary.replace(metadata)
        env = os.environ.copy()
        env.update(DISPLAY=server["display"], XAUTHORITY=server["authority"])
        return env


def main():
    if sys.argv[1:] == ["real-xclip"]:
        print(real_xclip())
        return
    action, state, *args = sys.argv[1:]
    env = clipboard_env(state)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("WAYLAND_SOCKET", None)
    if action == "run" and args:
        os.execvpe(args[0], args, env)
    elif action == "publish" and len(args) == 1:
        with open(args[0], "rb") as image:
            subprocess.run(
                [real_xclip(), "-selection", "clipboard", "-t", "image/png", "-i"],
                stdin=image,
                env=env,
                check=True,
                timeout=5,
                # xclip forks a clipboard owner. It must not retain the
                # caller's pipes and keep tmux's transfer waiting for EOF.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        raise RuntimeError("expected run <command...> or publish <image>")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"rpaste: {error}", file=sys.stderr)
        sys.exit(1)
