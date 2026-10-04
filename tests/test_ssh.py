import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import png, REPO
from rpaste_transport import interactive_shell, real_ssh, automatic_ssh


class ReverseSshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpaste-ssh-test-")
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        binaries = self.root / "bin"
        binaries.mkdir()
        for name, body in {
            "ssh": 'printf "%s\\n" "$@" > "$RPASTE_TEST_ARGS"\n/bin/cat "$RPASTE_TEST_IMAGE"\nexit "${RPASTE_TEST_EXIT:-0}"\n',
            "tmux": 'echo "SSH_CONNECTION=wrong-client 1 remote 22"\n',
            "tailscale": "exit 1\n",
            "who": 'echo "someone pts/99 (wrong-login)"\n',
        }.items():
            executable = binaries / name
            executable.write_text("#!/bin/sh\n" + body)
            executable.chmod(0o700)
        image = self.root / "source.png"
        image.write_bytes(png())
        self.env = os.environ.copy()
        for key in ("RPASTE_HOST", "RPASTE_SOCKET", "SSH_CONNECTION", "SSH_TTY"):
            self.env.pop(key, None)
        self.env.update(
            PATH=str(binaries) + os.pathsep + os.environ["PATH"],
            XDG_STATE_HOME=str(self.root / "state"),
            TMUX="fake-server",
            RPASTE_CLIENT_CONTEXT="1",
            RPASTE_TEST_IMAGE=str(image),
            RPASTE_TEST_ARGS=str(self.root / "ssh-args"),
        )

    def pull(self):
        return subprocess.run(
            [str(REPO / "rpaste"), "pull"], env=self.env, capture_output=True
        )

    def test_triggering_client_wins_over_stale_tmux_environment(self):
        self.env["SSH_CONNECTION"] = "correct-client 1 remote 22"
        result = self.pull()
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = (self.root / "ssh-args").read_text().splitlines()
        self.assertIn("correct-client", arguments)
        self.assertNotIn("wrong-client", arguments)

    def test_explicit_user_and_host_are_preserved(self):
        self.env.update(
            SSH_CONNECTION="wrong-client 1 remote 22",
            RPASTE_HOST="desktop-user@desktop",
        )
        result = self.pull()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "desktop-user@desktop", (self.root / "ssh-args").read_text().splitlines()
        )

    def test_failed_ssh_transfer_never_replaces_the_previous_image(self):
        self.env["RPASTE_HOST"] = "source"
        original = self.pull()
        self.assertEqual(original.returncode, 0, original.stderr)
        self.env["RPASTE_TEST_EXIT"] = "9"
        result = self.pull()
        self.assertNotEqual(result.returncode, 0)
        pointer = self.root / "state/rpaste/images/latest.png"
        self.assertEqual(str(pointer.resolve()), original.stdout.decode())
        self.assertEqual(list((self.root / "state/rpaste").glob("incoming-*")), [])

    def test_other_ssh_logins_are_not_used_as_a_source(self):
        result = self.pull()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "ssh-args").exists())


class AutomaticSshTests(unittest.TestCase):
    def test_ssh_wrapper_is_excluded_when_home_changes(self):
        wrapper = REPO.parent / ".local/bin/ssh"
        if not wrapper.exists():
            self.skipTest("dotfiles wrapper is not present in standalone checkout")
        with tempfile.TemporaryDirectory() as temporary:
            with patch("pathlib.Path.home", return_value=Path(temporary)), patch(
                "os.get_exec_path", return_value=[str(wrapper.parent), "/usr/bin"]
            ):
                self.assertEqual(
                    Path(real_ssh()).resolve(), Path("/usr/bin/ssh").resolve()
                )

    def test_existing_tunnel_is_not_wrapped_again(self):
        with patch("rpaste_transport.real_ssh", return_value="/usr/bin/ssh"), patch(
            "os.execv", side_effect=RuntimeError("delegated")
        ) as execute, patch("rpaste_transport.start") as start:
            with self.assertRaisesRegex(RuntimeError, "delegated"):
                automatic_ssh(
                    "source.sock",
                    "rpaste",
                    ["-o", "SetEnv=RPASTE_SOCKET=/tmp/already.sock", "host"],
                )
        start.assert_not_called()
        self.assertEqual(execute.call_args.args[1][-1], "host")

    def test_only_interactive_shell_connections_get_a_tunnel(self):
        for arguments in (
            ["host"],
            ["-p", "2222", "user@host"],
            ["-J", "bastion", "host"],
            ["-tt", "host"],
            ["--", "host"],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(interactive_shell(arguments))
        for arguments in (
            ["-V"],
            ["-G", "host"],
            ["host", "uptime"],
            ["-N", "host"],
            ["-O", "check", "host"],
            ["-s", "host", "sftp"],
            ["-W", "host:22", "bastion"],
        ):
            with self.subTest(arguments=arguments):
                self.assertFalse(interactive_shell(arguments))


if __name__ == "__main__":
    unittest.main()
