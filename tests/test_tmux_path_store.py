import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tmux_path_store as module


class TmuxPathStoreTests(unittest.TestCase):
    def setUp(self):
        self.g_script_patch = mock.patch.object(module, "g_script", "tmux-path-store")
        self.g_script_patch.start()
        self.addCleanup(self.g_script_patch.stop)
        self.python_exe = Path(f"/proc/{os.getpid()}/exe").resolve()

    def _run_cli(
        self,
        argv: list[str],
        *,
        home_dir: str,
        tmp_dir: str,
        user: str = "testuser",
        run_stdout: str | None = None,
        run_side_effect=None,
        check_output_return: bytes = b"window\n",
        check_output_side_effect=None,
    ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = {"HOME": home_dir, "TMPDIR": tmp_dir, "USER": user}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            if run_side_effect is not None:
                run_mock = stack.enter_context(mock.patch.object(module.subprocess, "run", side_effect=run_side_effect))
            else:
                run_mock = stack.enter_context(
                    mock.patch.object(
                        module.subprocess,
                        "run",
                        return_value=SimpleNamespace(stdout=run_stdout or f"{os.getpid()}\n"),
                    )
                )
            if check_output_side_effect is not None:
                check_output_mock = stack.enter_context(
                    mock.patch.object(module.subprocess, "check_output", side_effect=check_output_side_effect)
                )
            else:
                check_output_mock = stack.enter_context(
                    mock.patch.object(
                        module.subprocess,
                        "check_output",
                        return_value=check_output_return,
                    )
                )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            module.cli()
        return stdout.getvalue(), stderr.getvalue(), run_mock, check_output_mock

    def test_tmux_holder_path_uses_tmpdir_when_set(self):
        with mock.patch.dict(os.environ, {"TMPDIR": "/var/tmp/custom", "USER": "alice"}, clear=True):
            self.assertEqual(
                module.tmux_server_binary_holder_path(),
                Path("/var/tmp/custom/tmux-path-store_server_binary.alice"),
            )

    def test_tmux_holder_path_defaults_to_tmp_when_tmpdir_missing(self):
        with mock.patch.dict(os.environ, {"USER": "alice"}, clear=True):
            self.assertEqual(
                module.tmux_server_binary_holder_path(),
                Path("/tmp/tmux-path-store_server_binary.alice"),
            )

    def test_shell_words_quotes_spaces(self):
        self.assertEqual(module.shell_words("cd", "/tmp/work area"), "cd '/tmp/work area'")

    def test_cli_writes_tmux_cache_under_tmpdir(self):
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as tmp_dir:
            holder_path = Path(tmp_dir) / "tmux-path-store_server_binary.testuser"

            def fake_check_output(command, shell):
                self.assertTrue(shell)
                self.assertEqual(command, f"{self.python_exe} display-message -p '#W'")
                return b"window\n"

            stdout, stderr, run_mock, check_output_mock = self._run_cli(
                ["tmux-path-store", "list-windows"],
                home_dir=home_dir,
                tmp_dir=tmp_dir,
                check_output_side_effect=fake_check_output,
            )

            self.assertEqual(stderr, "")
            self.assertIn("Windows:", stdout)
            self.assertTrue(holder_path.exists())
            self.assertEqual(holder_path.read_text(), str(self.python_exe))
            run_mock.assert_called_once_with(
                ["/usr/bin/pgrep", "-f", "-u", "testuser", "tmux$"],
                check=False,
                capture_output=True,
                text=True,
            )
            check_output_mock.assert_called_once()

    def test_cli_reuses_cached_tmux_binary(self):
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as tmp_dir:
            holder_path = Path(tmp_dir) / "tmux-path-store_server_binary.testuser"
            holder_path.write_text(str(self.python_exe))

            def fake_check_output(command, shell):
                self.assertTrue(shell)
                self.assertEqual(command, f"{self.python_exe} display-message -p '#W'")
                return b"window\n"

            stdout, stderr, run_mock, check_output_mock = self._run_cli(
                ["tmux-path-store", "list-windows"],
                home_dir=home_dir,
                tmp_dir=tmp_dir,
                run_side_effect=AssertionError("pgrep should not be called when cache is valid"),
                check_output_side_effect=fake_check_output,
            )

            self.assertEqual(stderr, "")
            self.assertIn("Windows:", stdout)
            self.assertEqual(holder_path.read_text(), str(self.python_exe))
            check_output_mock.assert_called_once()
            self.assertEqual(run_mock.call_count, 0)

    def test_cli_replaces_stale_cached_tmux_binary(self):
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as tmp_dir:
            holder_path = Path(tmp_dir) / "tmux-path-store_server_binary.testuser"
            holder_path.write_text(str(Path(tmp_dir) / "missing-tmux"))

            def fake_check_output(command, shell):
                self.assertTrue(shell)
                self.assertEqual(command, f"{self.python_exe} display-message -p '#W'")
                return b"window\n"

            stdout, stderr, run_mock, check_output_mock = self._run_cli(
                ["tmux-path-store", "list-windows"],
                home_dir=home_dir,
                tmp_dir=tmp_dir,
                check_output_side_effect=fake_check_output,
            )

            self.assertEqual(stderr, "")
            self.assertIn("Windows:", stdout)
            self.assertEqual(holder_path.read_text(), str(self.python_exe))
            run_mock.assert_called_once_with(
                ["/usr/bin/pgrep", "-f", "-u", "testuser", "tmux$"],
                check=False,
                capture_output=True,
                text=True,
            )
            check_output_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
