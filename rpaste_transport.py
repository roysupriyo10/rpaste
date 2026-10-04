#!/usr/bin/env python3
"""Fetch clipboard images through a connection-specific SSH socket tunnel."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time

from rpaste_common import MAX_IMAGE, PNG_SIGNATURE, find_executable, spawn_detached


def real_ssh():
    # HOME can change under sudo/Homebrew or in isolated agent profiles. The
    # repository wrapper must still be excluded to avoid recursive execution.
    wrappers = (
        Path.home() / ".local/bin/ssh",
        Path(__file__).resolve().parent.parent / ".local/bin/ssh",
    )
    binary = find_executable(("ssh",), wrappers)
    if binary:
        return binary
    # The native client is present on both supported platforms.
    if Path("/usr/bin/ssh").is_file():
        return "/usr/bin/ssh"
    raise RuntimeError("the native SSH client is missing")


def interactive_shell(arguments):
    """Exclude scp/SFTP, commands, config queries, and forwarding-only clients."""
    options_with_value = set("BbcDEeFIiJLlmOopQRSWw")
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return index + 2 == len(arguments)
        if not argument.startswith("-") or argument == "-":
            return index + 1 == len(arguments)
        for offset, option in enumerate(argument[1:]):
            if option in "GNOVQsW":
                return False
            if option in options_with_value:
                if offset + 2 == len(argument):
                    index += 1
                break
        index += 1
    return False


def automatic_ssh(endpoint, bridge, arguments):
    binary = real_ssh()
    enabled = os.environ.get("RPASTE_AUTO", "1") != "0"
    already_tunneled = any(
        arg.removeprefix("-o").startswith("SetEnv=RPASTE_SOCKET=") for arg in arguments
    )
    if (
        not enabled
        or already_tunneled
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or not interactive_shell(arguments)
    ):
        os.execv(binary, [binary, *arguments])
    os.execv(binary, [binary, *tunnel_options(endpoint, bridge), *arguments])


def tunnel_options(endpoint, bridge):
    """The same connection setup is used by SSH and Kitty's SSH bootstrap."""
    start(endpoint, bridge)
    remote = f"/tmp/rpaste-{secrets.token_hex(12)}.sock"
    return [
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "StreamLocalBindMask=0177",
        "-o",
        f"SetEnv=RPASTE_SOCKET={remote}",
        "-R",
        f"{remote}:{endpoint}",
    ]


def request(endpoint, operation="image"):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(15)
        client.connect(endpoint)
        client.sendall((operation + "\n").encode())
        with client.makefile("rb") as stream:
            header = json.loads(stream.readline(4096))
            if not header.get("ok"):
                raise RuntimeError(header.get("error", "clipboard request failed"))
            size = header.get("size", 0)
            if not isinstance(size, int) or not 0 <= size <= MAX_IMAGE:
                raise RuntimeError("clipboard image exceeds 64 MiB")
            image = stream.read(size)
            if len(image) != size:
                raise RuntimeError("clipboard transfer was interrupted")
            return image


def serve(endpoint, bridge):
    path = Path(endpoint)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        # Only one source service owns the socket. Never unlink a live service.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if path.exists():
            path.unlink()
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(8)
            path.with_suffix(".pid").write_text(str(os.getpid()))

            def respond(connection):
                with connection:
                    connection.settimeout(15)
                    try:
                        with connection.makefile("rb") as stream:
                            operation = stream.readline(32).strip()
                        if operation == b"ping":
                            image = b""
                        elif operation in (b"image", b"text"):
                            result = subprocess.run(
                                [
                                    bridge,
                                    "get" if operation == b"image" else "get-text",
                                ],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                timeout=12,
                            )
                            if result.returncode:
                                raise RuntimeError(
                                    result.stderr.decode(errors="replace").strip()
                                )
                            image = result.stdout
                            if operation == b"image" and not image.startswith(
                                PNG_SIGNATURE
                            ):
                                raise RuntimeError("clipboard contains no PNG image")
                            if len(image) > MAX_IMAGE:
                                raise RuntimeError("clipboard image exceeds 64 MiB")
                        else:
                            raise RuntimeError("unknown clipboard request")
                        header = json.dumps(dict(ok=True, size=len(image))) + "\n"
                        connection.sendall(header.encode() + image)
                    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                        try:
                            connection.sendall(
                                (
                                    json.dumps(dict(ok=False, error=str(error))) + "\n"
                                ).encode()
                            )
                        except OSError:
                            pass

            while True:
                connection, _ = server.accept()
                threading.Thread(
                    target=respond, args=(connection,), daemon=True
                ).start()


def start(endpoint, bridge):
    try:
        request(endpoint, "ping")
        return
    except (OSError, ValueError, RuntimeError):
        pass
    path = Path(endpoint)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".log").open("ab") as log:
        spawn_detached(
            [sys.executable, str(Path(__file__).resolve()), "serve", endpoint, bridge],
            log,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            request(endpoint, "ping")
            return
        except (OSError, ValueError, RuntimeError):
            time.sleep(0.05)
    raise RuntimeError(
        f"clipboard source service failed to start; see {path.with_suffix('.log')}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("serve", "start"):
        p = subparsers.add_parser(action)
        p.add_argument("endpoint")
        p.add_argument("bridge")
    p = subparsers.add_parser("fetch")
    p.add_argument("endpoint")
    p.add_argument("--text", action="store_true")
    for action in ("ssh", "ssh-auto", "kitty-ssh"):
        p = subparsers.add_parser(action)
        p.add_argument("endpoint")
        p.add_argument("bridge")
        p.add_argument("ssh_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.endpoint, args.bridge)
    elif args.action == "start":
        start(args.endpoint, args.bridge)
    elif args.action == "fetch":
        sys.stdout.buffer.write(
            request(args.endpoint, "text" if args.text else "image")
        )
    elif args.action == "ssh-auto":
        automatic_ssh(args.endpoint, args.bridge, args.ssh_args)
    else:
        if not args.ssh_args:
            raise RuntimeError("usage: rpaste ssh [SSH options] user@host")
        options = tunnel_options(args.endpoint, args.bridge)
        if args.action == "kitty-ssh":
            os.execvp("kitty", ["kitty", "+kitten", "ssh", *options, *args.ssh_args])
        else:
            binary = real_ssh()
            os.execv(binary, [binary, "-t", *options, *args.ssh_args])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"rpaste: {error}", file=sys.stderr)
        sys.exit(1)
