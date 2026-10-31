import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tmux_path_store as module

REPO_ROOT = Path(__file__).resolve().parent.parent


class CoreRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tps-core-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE", "TMUX_WINDOW")
        }
        self.env.update(HOME=str(self.home), PYTHONPATH=str(REPO_ROOT))

    def run_cli(self, *args, cwd=None, env=None):
        return subprocess.run(
            [sys.executable, "-m", "tmux_path_store", *args],
            cwd=cwd or REPO_ROOT,
            env=env or {**self.env, "TMUX_WINDOW": "project"},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_relative_directory_is_saved_at_its_original_location(self):
        original = self.root / "original"
        original.mkdir()
        target = original / "saved"
        target.mkdir()
        stored = self.run_cli("set-dir", "1", "saved", cwd=original)
        self.assertEqual(stored.returncode, 0, stored.stderr)

        retrieved = self.run_cli("get-dir", "1", cwd=self.root)
        self.assertEqual(retrieved.returncode, 0, retrieved.stderr)
        self.assertEqual(retrieved.stdout.rstrip("\n"), str(target))

    def test_invalid_utf8_database_has_an_eval_friendly_failure(self):
        database = self.home / ".local/state/tmux_path_store/db.json"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"\xff")

        result = self.run_cli("cd", "1")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "false\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(database.read_bytes(), b"\xff")

    def test_invalid_utf8_cached_binary_is_discarded(self):
        holder = self.root / "tmux_server_binary"
        holder.write_bytes(b"\xff")
        try:
            self.assertIsNone(module.cached_tmux_server_binary(holder))
        except UnicodeDecodeError as exc:
            self.fail(f"invalid cache must be ignored: {exc}")
        self.assertFalse(holder.exists())

    def test_outside_tmux_uses_path_without_linux_process_discovery(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        tmux = binaries / "tmux"
        tmux.write_text("#!/bin/sh\nprintf 'path-window\\n'\n", encoding="utf-8")
        tmux.chmod(0o700)
        env = {**self.env, "PATH": str(binaries)}

        result = self.run_cli("set-dir", "0", "/tmp/from-path", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        database = self.home / ".local/state/tmux_path_store/db.json"
        self.assertEqual(json.loads(database.read_text())["path-window"]["dirs"][0], "/tmp/from-path")

    @unittest.skipUnless(Path("/proc/self/exe").exists(), "Linux /proc fallback")
    def test_discovery_recognizes_server_titles_and_launch_arguments(self):
        grep = shutil.which("grep")
        if grep is None:
            self.skipTest("grep is needed to exercise the pgrep expression")
        binaries = self.root / "bin"
        binaries.mkdir()
        pgrep = binaries / "pgrep"
        pgrep.write_text(
            f"#!{sys.executable}\n"
            "import os, subprocess, sys\n"
            f"result = subprocess.run([{grep!r}, '-Eq', sys.argv[-1]], "
            "input=os.environ['TPS_TEST_SERVER_COMMAND'], text=True)\n"
            f"if result.returncode == 0: print({os.getpid()})\n",
            encoding="utf-8",
        )
        pgrep.chmod(0o700)
        env = {**self.env, "PATH": str(binaries)}
        for command in (
            "tmux: server",
            "/usr/bin/tmux -L private -f /dev/null new-session -d -s project",
        ):
            with self.subTest(command=command):
                holder = self.home / ".local/state/tmux_path_store/tmux_server_binary"
                holder.unlink(missing_ok=True)
                with (
                    mock.patch.dict(os.environ, {**env, "TPS_TEST_SERVER_COMMAND": command}, clear=True),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    try:
                        binary = module.discover_tmux_server_binary()
                    except SystemExit:
                        self.fail(f"could not discover tmux with process command {command!r}")
                self.assertEqual(binary, Path(sys.executable).resolve())
                self.assertEqual(holder.read_text(), str(Path(sys.executable).resolve()))

    def start_tmux(self):
        binary = shutil.which("tmux")
        if binary is None:
            self.skipTest("tmux is not installed")
        socket = self.root / "private.sock"

        def tmux(*args):
            result = subprocess.run(
                [binary, "-S", str(socket), "-f", "/dev/null", *args],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
            )
            if args[0] == "new-session" and "Operation not permitted" in result.stderr:
                self.skipTest("the sandbox does not allow private tmux sockets")
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.removesuffix("\n")

        tmux("new-session", "-d", "-s", "core", "-n", "first", shlex.join(["sleep", "60"]))
        self.addCleanup(tmux, "kill-session", "-t", "core")
        pid = tmux("display-message", "-p", "#{pid}")
        return tmux, {**self.env, "TMUX": f"{socket},{pid},0"}

    def test_window_resolution_targets_the_invoking_inactive_pane(self):
        tmux, env = self.start_tmux()
        tmux("new-window", "-d", "-t", "core", "-n", "second", "sleep 60")
        pane = tmux("display-message", "-p", "-t", "core:second", "#{pane_id}")
        tmux("select-window", "-t", "core:first")

        with mock.patch.dict(os.environ, {**env, "TMUX_PANE": pane}, clear=True):
            self.assertEqual(module.resolve_window(), "second")

    def test_window_resolution_preserves_meaningful_trailing_whitespace(self):
        tmux, env = self.start_tmux()
        tmux("rename-window", "-t", "core:first", "project ")

        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(module.resolve_window(), "project ")


if __name__ == "__main__":
    unittest.main()
