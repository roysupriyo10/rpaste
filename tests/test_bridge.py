import concurrent.futures
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import rpaste_display
import rpaste_image
import rpaste_tmux
import rpaste_transport


def png(color=b"\xff\x00\x00"):
    def chunk(tag, data):
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data))
        )

    header = struct.pack("!IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    pixels = (b"\0" + color * 2) * 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


class ScratchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpaste-test-")
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)


class ImageTests(ScratchTest):
    def test_complete_image(self):
        rpaste_image.validate_png(png())

    def test_empty_text_truncated_and_corrupt_images_are_rejected(self):
        damaged = bytearray(png())
        damaged[20] ^= 1
        for data in (b"", b"SSH login banner", png()[:-12], bytes(damaged)):
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                rpaste_image.stage(self.root, io.BytesIO(data))
        self.assertFalse((self.root / "images/latest.png").exists())

    def test_failed_transfer_preserves_previous_image(self):
        previous = rpaste_image.stage(self.root, io.BytesIO(png()))
        with self.assertRaises(ValueError):
            rpaste_image.stage(self.root, io.BytesIO(png()[:20]))
        self.assertEqual((self.root / "images/latest.png").resolve(), previous)

    def test_parallel_transfers_are_immutable_and_unique(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            paths = list(
                workers.map(
                    lambda _: rpaste_image.stage(self.root, io.BytesIO(png())),
                    range(12),
                )
            )
        self.assertEqual(len(set(paths)), 12)
        for path in paths:
            self.assertEqual(path.read_bytes(), png())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_oversized_transfer_is_rejected(self):
        with patch.object(rpaste_image, "MAX_IMAGE", 16), self.assertRaises(ValueError):
            rpaste_image.stage(self.root, io.BytesIO(png()))


class TransportTests(ScratchTest):
    def setUp(self):
        super().setUp()
        self.image = self.root / "source.png"
        self.image.write_bytes(png())
        bridge = self.root / "bridge"
        bridge.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\nif sys.argv[1]=='get': sys.stdout.buffer.write(Path({str(self.image)!r}).read_bytes())\nelse: print('hello λ',end='')\n"
        )
        bridge.chmod(0o700)
        self.endpoint = str(self.root / "source.sock")
        self.server = subprocess.Popen(
            [
                sys.executable,
                str(REPO / "rpaste_transport.py"),
                "serve",
                self.endpoint,
                str(bridge),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 3
        while not Path(self.endpoint).exists():
            if self.server.poll() is not None or time.monotonic() > deadline:
                self.fail("fixture source server did not start")
            time.sleep(0.01)

    def stop_server(self):
        self.server.terminate()
        self.server.wait(timeout=3)

    def test_image_round_trip(self):
        self.assertEqual(rpaste_transport.request(self.endpoint), png())
        self.assertEqual(Path(self.endpoint).stat().st_mode & 0o777, 0o600)

    def test_text_round_trip(self):
        self.assertEqual(
            rpaste_transport.request(self.endpoint, "text"), "hello λ".encode()
        )

    def test_parallel_requests(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            images = list(
                workers.map(lambda _: rpaste_transport.request(self.endpoint), range(8))
            )
        self.assertEqual(images, [png()] * 8)

    def test_no_image_is_reported(self):
        self.image.write_bytes(b"")
        with self.assertRaisesRegex(RuntimeError, "no PNG image"):
            rpaste_transport.request(self.endpoint)

    def test_pull_uses_tunnel_before_reverse_ssh(self):
        environment = os.environ.copy()
        environment.pop("RPASTE_HOST", None)
        environment.update(
            RPASTE_SOCKET=self.endpoint,
            SSH_CONNECTION="wrong-source 1 remote 22",
            XDG_STATE_HOME=str(self.root / "remote"),
        )
        result = subprocess.run(
            [str(REPO / "rpaste"), "pull"],
            env=environment,
            capture_output=True,
            check=True,
        )
        self.assertEqual(Path(result.stdout.decode()).read_bytes(), png())


class ClientTests(ScratchTest):
    @unittest.skipUnless(
        sys.platform.startswith("linux") or sys.platform == "darwin",
        "process environment lookup requires Linux/macOS",
    )
    def test_source_belongs_to_the_client_that_pressed_the_key(self):
        clients = []
        try:
            for source in ("first", "second"):
                env = os.environ.copy()
                env.update(
                    RPASTE_SOCKET=f"/tmp/{source}.sock",
                    SSH_CONNECTION=f"{source} 1 remote 22",
                )
                clients.append(
                    subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(30)"], env=env
                    )
                )
            with patch.dict(
                os.environ,
                {"RPASTE_SOCKET": "/tmp/stale.sock", "SSH_TTY": "/dev/stale"},
            ):
                for client, source in zip(clients, ("first", "second")):
                    context = rpaste_tmux.client_environment(client.pid)
                    self.assertEqual(context["RPASTE_SOCKET"], f"/tmp/{source}.sock")
                    self.assertEqual(context["SSH_CONNECTION"].split()[0], source)
                    self.assertEqual(context["RPASTE_CLIENT_CONTEXT"], "1")
        finally:
            for client in clients:
                client.terminate()
                client.wait(timeout=3)


class TmuxTests(ScratchTest):
    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_bracketed_paste_targets_one_pane_and_preserves_the_draft(self):
        name = "rpaste-test-" + secrets.token_hex(6)
        script = self.root / "capture.py"
        received = self.root / "received"
        ready = self.root / "ready"
        script.write_text(
            "import os,tty\nfrom pathlib import Path\ntty.setraw(0)\nos.write(1,b'\\x1b[?2004h')\n"
            + f"Path({str(ready)!r}).touch()\n"
            + f"with open({str(received)!r},'ab',buffering=0) as out:\n while True:\n  out.write(os.read(0,4096))\n"
        )
        subprocess.run(
            [
                "tmux",
                "-L",
                name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "test",
                sys.executable,
                str(script),
            ],
            check=True,
        )
        self.addCleanup(
            lambda: subprocess.run(
                ["tmux", "-L", name, "kill-server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        socket_path = (
            subprocess.check_output(
                ["tmux", "-L", name, "display-message", "-p", "#{socket_path}"]
            )
            .decode()
            .strip()
        )
        environment = dict(TMUX=f"{socket_path},0,0")
        deadline = time.monotonic() + 3
        while not ready.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        subprocess.run(["tmux", "-L", name, "send-keys", "-l", "draft: "], check=True)
        # Focus another pane before delivering, as can happen while a
        # clipboard transfer runs in the background.
        subprocess.run(["tmux", "-L", name, "split-window", "sleep 30"], check=True)
        with patch.dict(os.environ, environment):
            rpaste_tmux.paste_text("%0", b'"/tmp/image with spaces.png"')
        expected = b'draft: \x1b[200~"/tmp/image with spaces.png"\x1b[201~'
        while not received.exists() or received.read_bytes() != expected:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(received.read_bytes(), expected)
        self.assertNotIn(b"\r", received.read_bytes())
        self.assertEqual(
            subprocess.check_output(["tmux", "-L", name, "list-buffers"]), b""
        )


class DeliveryTests(ScratchTest):
    def test_expected_clipboard_failure_keeps_its_explanation_visible(self):
        error = RuntimeError(
            "rpaste: pulling clipboard from source\nrpaste: no image in clipboard (xclip)"
        )
        with patch.object(
            subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ) as run, patch("sys.stderr", new=io.StringIO()) as stderr:
            self.assertTrue(rpaste_tmux.report_error(error, "%28"))
        self.assertIn("no image in clipboard", run.call_args.args[0][-1])
        self.assertNotIn("rpaste: rpaste:", stderr.getvalue())

    def test_reporting_failure_retains_nonzero_status(self):
        with patch.object(
            subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
        ), patch("sys.stderr", new=io.StringIO()):
            self.assertFalse(
                rpaste_tmux.report_error(RuntimeError("source unreachable"), "%28")
            )

    def test_native_mode_requires_registered_clipboard_before_sending_key(self):
        with patch.object(rpaste_tmux, "tmux") as tmux, self.assertRaisesRegex(
            RuntimeError, "--rpaste-native"
        ):
            rpaste_tmux.paste_native(
                "rpaste", "%0", {"agent": "codex"}, self.root / "image.png", {}
            )
        tmux.assert_not_called()

    def test_dead_registration_does_not_capture_keys_in_a_shell(self):
        with patch.object(
            rpaste_tmux,
            "tmux",
            side_effect=[json.dumps(dict(agent="codex", pid=1)), "zsh"],
        ), patch.object(os, "kill", side_effect=ProcessLookupError):
            self.assertIsNone(rpaste_tmux.pane_agent("%0"))

    def test_remote_client_does_not_paste_from_a_local_agents_clipboard(self):
        with patch.object(rpaste_tmux, "tmux") as tmux, self.assertRaisesRegex(
            RuntimeError, "from SSH"
        ):
            rpaste_tmux.paste_native(
                "rpaste",
                "%0",
                {"native_local": True},
                self.root / "image.png",
                {"SSH_CONNECTION": "source 1 remote 22"},
            )
        tmux.assert_not_called()


class NativeTests(ScratchTest):
    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and shutil.which("Xvfb")
        and shutil.which("/usr/bin/xclip"),
        "optional native X11 dependencies are absent",
    )
    def test_native_clipboards_are_authenticated_and_isolated(self):
        states = [self.root / "first", self.root / "second"]
        environments = []
        try:
            for state, color in zip(states, (b"\xff\0\0", b"\0\xff\0")):
                image = state.with_suffix(".png")
                image.write_bytes(png(color))
                subprocess.run(
                    [
                        sys.executable,
                        str(REPO / "rpaste_display.py"),
                        "publish",
                        str(state),
                        str(image),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=8,
                )
                environments.append(rpaste_display.clipboard_env(state))
            self.assertNotEqual(environments[0]["DISPLAY"], environments[1]["DISPLAY"])
            for env, color in zip(environments, (b"\xff\0\0", b"\0\xff\0")):
                image = subprocess.check_output(
                    [
                        "/usr/bin/xclip",
                        "-selection",
                        "clipboard",
                        "-t",
                        "image/png",
                        "-o",
                    ],
                    env=env,
                )
                self.assertEqual(image, png(color))
                self.assertEqual(Path(env["XAUTHORITY"]).stat().st_mode & 0o777, 0o600)
        finally:
            for state in states:
                metadata = state / "clipboard/display.json"
                if metadata.exists():
                    os.kill(json.loads(metadata.read_text())["pid"], signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
