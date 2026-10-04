#!/usr/bin/env python3
"""Launch an agent with a pane-specific native clipboard and registration."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from rpaste_display import clipboard_env, real_xclip
from rpaste_common import SUPPORTED_AGENTS


def native_environment(state, environment):
    """Return None when optional native tools are absent; path paste still works."""
    if sys.platform.startswith("linux"):
        if not shutil.which("Xvfb"):
            return None
        try:
            real_xclip()
        except RuntimeError:
            return None
        environment = clipboard_env(state)
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("WAYLAND_SOCKET", None)
    elif sys.platform == "darwin":
        base = (
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "rpaste"
        )
        dylib = base / "rpaste-inject.dylib"
        if not dylib.is_file():
            return None
        libraries = environment.get("DYLD_INSERT_LIBRARIES", "").split(":")
        environment["DYLD_INSERT_LIBRARIES"] = ":".join(
            dict.fromkeys([str(dylib), *filter(None, libraries)])
        )
    else:
        return None
    environment["RPASTE_NATIVE_STATE"] = state
    environment["RPASTE_IMAGES_DIR"] = str(Path(state) / "images")
    return environment


def main():
    state, *command = sys.argv[1:]
    if not command:
        raise RuntimeError("usage: rpaste run -- command [args...]")
    environment = os.environ.copy()
    pane = environment.get("TMUX_PANE")
    registration = dict(agent=Path(command[0]).name, pid=os.getpid(), native_local=True)
    remote = bool(environment.get("SSH_CONNECTION") or environment.get("RPASTE_SOCKET"))
    if remote:
        if pane and environment.get("TMUX"):
            server = hashlib.sha256(
                environment["TMUX"].split(",")[0].encode()
            ).hexdigest()[:12]
            state = str(Path(state) / "panes" / (server + "-" + pane.lstrip("%")))
        registration["native_local"] = False
        try:
            prepared = native_environment(state, environment)
        except (OSError, RuntimeError) as error:
            registration["native_error"] = str(error)
            prepared = None
        if prepared:
            environment = prepared
            registration["native_state"] = state
    if pane and environment.get("TMUX") and registration["agent"] in SUPPORTED_AGENTS:
        subprocess.run(
            [
                "tmux",
                "set-option",
                "-p",
                "-t",
                pane,
                "@rpaste-agent",
                json.dumps(registration),
            ],
            check=True,
        )
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"rpaste: {error}", file=sys.stderr)
        sys.exit(1)
