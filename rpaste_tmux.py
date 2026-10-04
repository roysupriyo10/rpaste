#!/usr/bin/env python3
"""Deliver an image to the exact tmux pane and client that requested it."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from rpaste_common import SUPPORTED_AGENTS

SOURCE_VARIABLES = (
    "RPASTE_SOCKET",
    "RPASTE_HOST",
    "SSH_CONNECTION",
    "SSH_CLIENT",
    "SSH_TTY",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "WAYLAND_SOCKET",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
)


def tmux(*args, input=None):
    return (
        subprocess.run(
            ["tmux", *args],
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def client_environment(pid):
    """The server/session environment may belong to a different attached client."""
    environment = os.environ.copy()
    for key in SOURCE_VARIABLES:
        environment.pop(key, None)
    if sys.platform.startswith("linux"):
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        values = dict(
            entry.decode(errors="surrogateescape").split("=", 1)
            for entry in entries
            if b"=" in entry
        )
    elif sys.platform == "darwin":
        # Darwin exposes process environments through ps rather than /proc.
        output = subprocess.check_output(
            ["ps", "eww", "-p", str(pid), "-o", "command="]
        ).decode()
        values = {}
        for key in SOURCE_VARIABLES:
            pattern = r"(?:^|\s)" + key + r"=(\S+)"
            if key == "SSH_CONNECTION":
                pattern = r"(?:^|\s)SSH_CONNECTION=(\S+ \d+ \S+ \d+)"
            match = re.search(pattern, output)
            if match:
                values[key] = match.group(1)
    else:
        raise RuntimeError("client environment lookup supports Linux and macOS")
    environment.update((key, values[key]) for key in SOURCE_VARIABLES if key in values)
    environment["RPASTE_CLIENT_CONTEXT"] = "1"
    return environment


def pane_agent(pane):
    metadata = tmux("show-options", "-pqv", "-t", pane, "@rpaste-agent")
    if metadata:
        try:
            registration = json.loads(metadata)
            os.kill(registration["pid"], 0)
            if registration.get("agent") in SUPPORTED_AGENTS:
                return registration
        except (OSError, ValueError, KeyError):
            pass
    command = tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    if command in SUPPORTED_AGENTS:
        return dict(agent=command)
    return None


def get_image(bridge, environment):
    command = (
        "pull"
        if environment.get("RPASTE_SOCKET")
        or environment.get("RPASTE_HOST")
        or environment.get("SSH_CONNECTION")
        else "local"
    )
    result = subprocess.run(
        [bridge, command],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=25,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    path = Path(result.stdout.decode().strip())
    if not path.is_file():
        raise RuntimeError("clipboard transfer produced no image file")
    return path


def paste_text(pane, text):
    # A named buffer prevents interference with the user's ordinary tmux
    # clipboard. -p preserves the agent's bracketed-paste attachment handling.
    buffer = "rpaste-" + secrets.token_hex(8)
    tmux("load-buffer", "-b", buffer, "-", input=text)
    try:
        tmux("paste-buffer", "-p", "-d", "-b", buffer, "-t", pane)
    finally:
        subprocess.run(
            ["tmux", "delete-buffer", "-b", buffer],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def paste_native(bridge, pane, registration, image, environment):
    if registration.get("native_local"):
        if environment.get("SSH_CONNECTION") or environment.get("RPASTE_SOCKET"):
            raise RuntimeError(
                "restart this locally launched agent from SSH to enable its private clipboard"
            )
        tmux("send-keys", "-t", pane, "C-v")
        return
    state = registration.get("native_state")
    if not state:
        if registration.get("native_error"):
            raise RuntimeError(registration["native_error"])
        raise RuntimeError(
            "run dotfiles/install.sh --rpaste-native, then restart the agent to enable native paste"
        )
    subprocess.run(
        [bridge, "publish", str(image), state],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    tmux("send-keys", "-t", pane, "C-v")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pane", required=True)
    parser.add_argument("--client", type=int, required=True)
    parser.add_argument("--method", choices=("path", "native"))
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    bridge = str(Path(__file__).with_name("rpaste"))
    registration = pane_agent(args.pane)
    if not registration:
        tmux("send-keys", "-t", args.pane, "C-v")
        return
    method = (
        args.method
        or tmux("show-options", "-qv", "-t", args.pane, "@rpaste-method")
        or "path"
    )
    if method not in ("path", "native"):
        raise RuntimeError("@rpaste-method must be path or native")
    environment = client_environment(args.client)
    root = (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "rpaste"
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Do not queue repeated presses and accidentally attach the same image
    # several times while a slow SSH transfer is still in progress.
    lock_name = re.sub(r"[^a-zA-Z0-9_-]", "_", os.environ.get("TMUX", "") + args.pane)
    with (root / f"paste-{lock_name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            image = args.file.resolve() if args.file else get_image(bridge, environment)
        except RuntimeError as image_error:
            # Preserve text pasting in agent panes without forwarding Ctrl+V
            # to a virtual clipboard that may still hold an older image.
            command = (
                "pull-text"
                if any(
                    environment.get(key)
                    for key in ("RPASTE_SOCKET", "RPASTE_HOST", "SSH_CONNECTION")
                )
                else "get-text"
            )
            result = subprocess.run(
                [bridge, command],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=25,
            )
            if result.returncode or not result.stdout:
                raise image_error
            result.stdout.decode("utf-8")
            paste_text(args.pane, result.stdout)
            return
        if method == "path":
            paste_text(args.pane, json.dumps(str(image), ensure_ascii=False).encode())
        else:
            paste_native(bridge, args.pane, registration, image, environment)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = (
            error.stderr.decode(errors="replace").strip()
            if isinstance(error, subprocess.CalledProcessError) and error.stderr
            else str(error)
        )
        message = f"rpaste: {detail}".replace("\n", " ")[:500]
        # A tmux status message leaves the user's draft and terminal untouched.
        pane = sys.argv[sys.argv.index("--pane") + 1] if "--pane" in sys.argv else None
        if pane:
            subprocess.run(
                ["tmux", "display-message", "-t", pane, "-d", "6000", "--", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        print(message, file=sys.stderr)
        sys.exit(1)
