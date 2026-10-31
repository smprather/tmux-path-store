import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tmux_path_store as module

REPO_ROOT = Path(__file__).resolve().parent.parent


class TmuxPathStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.g_script_patch = mock.patch.object(module, "g_script", "tmux-path-store")
        self.g_script_patch.start()
        self.addCleanup(self.g_script_patch.stop)
        self.python_exe = Path(sys.executable).resolve()
        self.home_dir = tempfile.mkdtemp(prefix="tps-home-")
        self.addCleanup(shutil.rmtree, self.home_dir, ignore_errors=True)

    # ------------------------------------------------------------------ helpers

    def _state_dir(self, home_dir=None):
        return Path(home_dir or self.home_dir) / ".local" / "state" / module.STATE_DIR_NAME

    def _db_file(self, home_dir=None):
        return self._state_dir(home_dir) / "db.json"

    def _write_db(self, db, home_dir=None, versioned=True):
        path = self._db_file(home_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"version": module.DB_VERSION, "windows": db} if versioned else db
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _read_db(self, home_dir=None):
        return json.loads(self._db_file(home_dir).read_text(encoding="utf-8"))

    def _read_windows(self, home_dir=None):
        return self._read_db(home_dir)["windows"]

    def _seed(self):
        return {
            "alpha": {
                "dirs": {"0": {"path": "/tmp/a0"}},
                "files": {"0": {"path": "/tmp/f0"}},
            },
            "beta": {"dirs": {"0": {"path": "/tmp/b0"}}, "files": {}},
        }

    def _seed_legacy(self):
        return {
            "alpha": {"dirs": ["/tmp/a0"] + [""] * 9, "files": ["/tmp/f0"] + [""] * 9},
            "beta": {"dirs": ["/tmp/b0"] + [""] * 9, "files": [""] * 10},
        }

    def _run_cli(
        self,
        argv: list[str],
        *,
        db: dict | None = None,
        versioned: bool = True,
        home_dir: str | Path | None = None,
        user: str | None = "testuser",
        tmux_window: str | None = "alpha",
        check_output_return: str = "window\n",
        check_output_side_effect=None,
        run_stdout: str | None = None,
        run_side_effect=None,
        pgrep_path: str | None = "/usr/bin/pgrep",
        env_extra: dict | None = None,
        chdir: str | Path | None = None,
    ):
        home_dir = Path(home_dir or self.home_dir)
        if db is not None:
            self._write_db(db, home_dir, versioned=versioned)
        stdout, stderr = io.StringIO(), io.StringIO()
        env = {"HOME": str(home_dir), "PATH": os.environ.get("PATH", "")}
        if user is not None:
            env["USER"] = user
        if tmux_window is not None:
            env["TMUX_WINDOW"] = tmux_window
        env.update(env_extra or {})
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            stack.enter_context(
                mock.patch.object(
                    module.shutil, "which", side_effect=lambda name: pgrep_path if name == "pgrep" else None
                )
            )
            if run_side_effect is not None:
                run_mock = stack.enter_context(
                    mock.patch.object(module.subprocess, "run", side_effect=run_side_effect)
                )
            else:
                run_stdout = run_stdout if run_stdout is not None else f"{os.getpid()}\n"
                run_mock = stack.enter_context(
                    mock.patch.object(
                        module.subprocess,
                        "run",
                        return_value=SimpleNamespace(stdout=run_stdout, returncode=0),
                    )
                )
            if check_output_side_effect is not None:
                check_output_mock = stack.enter_context(
                    mock.patch.object(module.subprocess, "check_output", side_effect=check_output_side_effect)
                )
            else:
                check_output_mock = stack.enter_context(
                    mock.patch.object(module.subprocess, "check_output", return_value=check_output_return)
                )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            if chdir is not None:
                stack.enter_context(contextlib.chdir(chdir))
            rc = 0
            try:
                module.cli()
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return SimpleNamespace(
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            rc=rc,
            run_mock=run_mock,
            check_output_mock=check_output_mock,
            db_file=self._db_file(home_dir),
        )

    def _expect_clean_failure(
        self,
        argv: list[str],
        needle: str,
        *,
        db: dict | None = None,
        tmux_window: str | None = "alpha",
        before: bytes | None = None,
    ):
        result = self._run_cli(["tmux-path-store", *argv], db=db, tmux_window=tmux_window)
        self.assertEqual(result.rc, 1, msg=f"{argv}: expected rc 1, got {result.rc}")
        self.assertEqual(result.stdout, "", msg=f"{argv}: errors must not write to stdout")
        self.assertIn(needle, result.stderr, msg=f"{argv}: {result.stderr!r}")
        self.assertNotIn("Traceback", result.stderr, msg=f"{argv}: raw traceback leaked")
        if before is not None:
            self.assertEqual(self._db_file().read_bytes(), before, msg=f"{argv}: db was modified")
        return result

    def _workdir(self, prefix):
        path = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    # ------------------------------------------------------- helpers / units

    def test_script_name_falls_back_for_interpreter_entry_points(self):
        for argv0 in ("", "/usr/lib/python3.14/__init__.py", "/somewhere/__main__.py", "-c", "-m"):
            with self.subTest(argv0=argv0):
                self.assertEqual(module.script_name(argv0), module.DISTRIBUTION_NAME)
        self.assertEqual(module.script_name("/usr/local/bin/tps"), "tps")

    def test_parse_index_accepts_0_through_9(self):
        for raw in ("0", "9"):
            with self.subTest(raw=raw):
                self.assertEqual(module.parse_index(raw), int(raw))

    def test_parse_index_rejects_junk_and_out_of_range(self):
        for raw, needle in (
            ("x", "not a valid"),
            ("", "not a valid"),
            ("10", "out of range"),
            ("-1", "out of range"),
        ):
            with self.subTest(raw=raw):
                out, err = io.StringIO(), io.StringIO()
                with (
                    contextlib.redirect_stdout(out),
                    contextlib.redirect_stderr(err),
                    self.assertRaises(SystemExit),
                ):
                    module.parse_index(raw)
                self.assertEqual(out.getvalue(), "")
                self.assertIn(needle, err.getvalue())

    def test_min_python_matches_packaging(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            requires_python = tomllib.load(fh)["project"]["requires-python"]
        self.assertEqual(
            module.MIN_PYTHON,
            tuple(int(part) for part in requires_python.removeprefix(">=").split(".")),
        )

    def test_packaging_version_is_calver(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            version = tomllib.load(fh)["project"]["version"]
        self.assertRegex(version, r"^[0-9]{4}\.(?:[1-9]|1[0-2])\.[1-9][0-9]*$")

    # ------------------------------------------------------- schema / migration

    def test_saved_database_is_versioned_and_sparse(self):
        result = self._run_cli(["tmux-path-store", "set-dir", "3", "/tmp/x"], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        document = self._read_db()
        self.assertEqual(document["version"], module.DB_VERSION)
        alpha = document["windows"]["alpha"]
        self.assertEqual(alpha["dirs"]["3"]["path"], "/tmp/x")
        self.assertEqual(alpha["dirs"]["0"]["path"], "/tmp/a0")
        self.assertNotIn("1", alpha["dirs"])

    def test_legacy_list_database_is_migrated_on_write(self):
        result = self._run_cli(
            ["tmux-path-store", "set-dir", "1", "/tmp/x"],
            db=self._seed_legacy(),
            versioned=False,
        )
        self.assertEqual(result.rc, 0, result.stderr)
        document = self._read_db()
        self.assertEqual(document["version"], module.DB_VERSION)
        self.assertEqual(document["windows"]["alpha"]["dirs"]["0"]["path"], "/tmp/a0")
        self.assertEqual(document["windows"]["alpha"]["dirs"]["1"]["path"], "/tmp/x")
        self.assertNotIn("2", document["windows"]["alpha"]["dirs"])

    def test_unsupported_database_version_fails_cleanly(self):
        path = self._db_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 99, "windows": {}}), encoding="utf-8")
        before = path.read_bytes()
        result = self._run_cli(["tmux-path-store", "set-dir", "0", "/tmp/x"])
        self.assertEqual(result.rc, 1)
        self.assertIn("version", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_v1_sparse_database_is_migrated_to_entries(self):
        path = self._db_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "windows": {"alpha": {"dirs": {"0": "/tmp/a0"}, "files": {}}}}),
            encoding="utf-8",
        )
        result = self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/x"])
        self.assertEqual(result.rc, 0, result.stderr)
        document = self._read_db()
        self.assertEqual(document["version"], module.DB_VERSION)
        self.assertEqual(document["windows"]["alpha"]["dirs"]["0"], {"path": "/tmp/a0"})

    def test_saving_a_slot_records_saved_at(self):
        with mock.patch.object(module.time, "time_ns", return_value=111):
            result = self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/x"], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertEqual(
            self._read_windows()["alpha"]["dirs"]["1"],
            {"path": "/tmp/x", "saved_at": 111},
        )

    def test_get_dir_records_last_used(self):
        with mock.patch.object(module.time, "time_ns", return_value=222):
            result = self._run_cli(["tmux-path-store", "get-dir", "0"], db=self._seed())
        self.assertEqual(result.stdout, "/tmp/a0\n")
        entry = self._read_windows()["alpha"]["dirs"]["0"]
        self.assertEqual(entry, {"path": "/tmp/a0", "last_used": 222})

    def test_open_files_records_last_used_for_selected_slots(self):
        with mock.patch.object(module.time, "time_ns", return_value=333):
            result = self._run_cli(["tmux-path-store", "open-files", "0"], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        entry = self._read_windows()["alpha"]["files"]["0"]
        self.assertEqual(entry, {"path": "/tmp/f0", "last_used": 333})

    def test_recent_orders_by_last_used_then_saved_at(self):
        db = {
            "alpha": {
                "dirs": {
                    "0": {"path": "/d0", "saved_at": 10, "last_used": 50},
                    "1": {"path": "/d1", "saved_at": 30},
                    "2": {"path": "/d2", "saved_at": 20, "last_used": 40},
                },
                "files": {"0": {"path": "/f0", "saved_at": 60}},
            }
        }
        result = self._run_cli(["tmux-path-store", "recent"], db=db)
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["file 0 /f0", "dir 0 /d0", "dir 2 /d2", "dir 1 /d1"],
        )

    def test_recent_accepts_a_limit(self):
        db = {
            "alpha": {
                "dirs": {"0": {"path": "/d0", "last_used": 50}, "1": {"path": "/d1", "last_used": 40}},
                "files": {},
            }
        }
        result = self._run_cli(["tmux-path-store", "recent", "1"], db=db)
        self.assertEqual(result.stdout.splitlines(), ["dir 0 /d0"])

    def test_recent_rejects_a_negative_limit(self):
        before = self._write_db(self._seed()).read_bytes()
        self._expect_clean_failure(["recent", "-1"], "out of range", before=before)

    # ------------------------------------------------ state dir / discovery

    def test_state_dir_honors_xdg_state_home(self):
        with mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": "/tmp/tps-xdg-state", "HOME": "/tmp/tps-home"}, clear=True
        ):
            self.assertEqual(
                module.state_dir(), Path("/tmp/tps-xdg-state") / module.STATE_DIR_NAME
            )

    def test_state_dir_ignores_relative_xdg_state_home(self):
        with mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": "relative/state", "HOME": "/tmp/tps-home"}, clear=True
        ):
            self.assertEqual(
                module.state_dir(), Path("/tmp/tps-home/.local/state") / module.STATE_DIR_NAME
            )

    def test_state_dir_uses_password_database_when_home_is_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                module.state_dir(), Path.home() / ".local/state" / module.STATE_DIR_NAME
            )

    def test_lock_file_is_private(self):
        result = self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/new"], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        lock = self._state_dir() / "db.lock"
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_window_independent_commands_do_not_query_tmux(self):
        for argv in (
            ["list-windows"],
            ["list-dirs", "alpha"],
            ["list-files", "beta"],
            ["delete-window", "beta"],
        ):
            with self.subTest(argv=argv):
                result = self._run_cli(
                    ["tmux-path-store", *argv],
                    db=self._seed(),
                    tmux_window=None,
                    run_side_effect=AssertionError("tmux discovery must not run"),
                    check_output_side_effect=AssertionError("tmux must not be queried"),
                )
                self.assertEqual(result.rc, 0, result.stderr)

    def test_all_windows_listing_works_when_tmux_is_unreachable(self):
        result = self._run_cli(
            ["tmux-path-store", "list-dirs", "__all__"],
            db=self._seed(),
            tmux_window=None,
            check_output_side_effect=subprocess.CalledProcessError(1, "tmux"),
        )
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertIn("Window: alpha", result.stdout)
        self.assertIn("Window: beta", result.stdout)

    def test_inside_tmux_uses_the_server_pid_binary(self):
        with mock.patch.dict(os.environ, {"TMUX": f"/tmp/tps-sock,{os.getpid()},0"}, clear=True):
            self.assertEqual(module.find_tmux_binary(), self.python_exe)

    def test_outside_tmux_uses_tmux_on_path(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                module.shutil,
                "which",
                side_effect=lambda name: self.python_exe if name == "tmux" else None,
            ),
        ):
            self.assertEqual(module.find_tmux_binary(), self.python_exe)

    def test_pgrep_fallback_uses_the_server_process_binary(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                module.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/pgrep" if name == "pgrep" else None,
            ),
            mock.patch.object(
                module.subprocess, "run", return_value=SimpleNamespace(stdout=f"{os.getpid()}\n")
            ) as run_mock,
        ):
            self.assertEqual(module.find_tmux_binary(), self.python_exe)
        run_mock.assert_called_once_with(
            ["/usr/bin/pgrep", "-f", "-u", mock.ANY, r"(^|/)tmux([[:space:]:]|$)"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_missing_pgrep_returns_no_binary(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(module.shutil, "which", return_value=None),
        ):
            self.assertIsNone(module.find_tmux_binary())

    # ------------------------------------------------------- argument errors

    def test_invalid_arguments_fail_cleanly(self):
        cases = (
            (["get-dir", "x"], "not a valid"),
            (["get-dir", "10"], "out of range"),
            (["set-dir"], "Usage:"),
            (["set-dir", "12", "/tmp"], "out of range"),
            (["set-dir", "-1", "/tmp"], "out of range"),
            (["set-dir", "1", "/tmp", "extra"], "Usage:"),
            (["set-file"], "Usage:"),
            (["set-file", "10", "/tmp/x"], "out of range"),
            (["set-file", "1", "/tmp", "extra"], "Usage:"),
            (["reset-dir", "99"], "out of range"),
            (["reset-file", "abc"], "not a valid"),
            (["get-dir"], "Usage:"),
            (["get-file", "10"], "out of range"),
            (["swap-dirs", "1"], "Usage:"),
            (["swap-dirs", "1", "2", "3"], "Usage:"),
            (["swap-files", "-1", "0"], "out of range"),
            (["open-files"], "Usage:"),
            (["open-files", "11"], "out of range"),
        )
        for argv, needle in cases:
            with self.subTest(argv=argv):
                db = self._seed()
                before = self._write_db(db).read_bytes()
                self._expect_clean_failure(argv, needle, before=before)

    def test_negative_index_does_not_alias_a_real_slot(self):
        db = self._seed()
        db["alpha"]["dirs"]["9"] = "/tmp/slot9"
        before = self._write_db(db).read_bytes()
        result = self._expect_clean_failure(["get-dir", "-1"], "out of range", before=before)
        self.assertNotIn("/tmp/slot9", result.stdout)

    def test_removed_compatibility_commands_are_rejected(self):
        for argv in (["cd", "0"], ["edit-file", "0"], ["edit-files", "0"]):
            with self.subTest(argv=argv):
                result = self._run_cli(["tmux-path-store", *argv], db=self._seed())
                self.assertEqual(result.rc, 1)
                self.assertIn("Unrecognized sub-command", result.stderr)
                self.assertEqual(result.stdout, "")

    # ---------------------------------------------------------- db robustness

    def test_corrupt_db_fails_cleanly_and_is_never_overwritten(self):
        for content in ("not json", "", "[]", "null", '"oops"'):
            with self.subTest(content=content):
                path = self._db_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                before = path.read_bytes()
                result = self._run_cli(["tmux-path-store", "list-windows"], tmux_window="ghost")
                self.assertEqual(result.rc, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("db.json", result.stderr)
                self.assertEqual(path.read_bytes(), before)

    def test_drifted_entries_are_padded_and_readable(self):
        db = {"alpha": {"dirs": ["/tmp/only"], "files": []}}
        result = self._run_cli(["tmux-path-store", "list-dirs"], db=db)
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertEqual(len([line for line in result.stdout.splitlines() if line.startswith("  ")]), 10)
        result = self._run_cli(["tmux-path-store", "list-files"], db=db)
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertIn("Window: alpha", result.stdout)

    def test_non_object_window_entry_does_not_crash(self):
        result = self._run_cli(["tmux-path-store", "list-dirs"], db={"alpha": "oops"})
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertEqual(len([line for line in result.stdout.splitlines() if line.startswith("  ")]), 10)

    def test_non_string_slot_values_become_empty(self):
        result = self._run_cli(
            ["tmux-path-store", "list-dirs"], db={"alpha": {"dirs": [1, None, "/tmp/x"], "files": []}}
        )
        self.assertEqual(result.rc, 0)
        self.assertIn("  0: \n", result.stdout)
        self.assertIn("  2: /tmp/x", result.stdout)

    # -------------------------------------------------------- gate / listings

    def test_all_windows_listing_works_when_current_window_is_absent(self):
        result = self._run_cli(
            ["tmux-path-store", "list-dirs", "__all__"], db=self._seed(), tmux_window="ghost"
        )
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertIn("Window: alpha", result.stdout)
        self.assertIn("Window: beta", result.stdout)

    def test_current_window_is_listed_last_in_all_windows_mode(self):
        result = self._run_cli(
            ["tmux-path-store", "list-dirs", "__all__"], db=self._seed(), tmux_window="alpha"
        )
        self.assertEqual(result.rc, 0)
        self.assertLess(result.stdout.index("Window: beta"), result.stdout.index("Window: alpha"))

    def test_explicit_window_listing_works_when_current_window_is_absent(self):
        result = self._run_cli(
            ["tmux-path-store", "list-files", "beta"], db=self._seed(), tmux_window="ghost"
        )
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertIn("Window: beta", result.stdout)

    def test_list_windows_works_when_current_window_is_absent(self):
        result = self._run_cli(["tmux-path-store", "list-windows"], db=self._seed(), tmux_window="ghost")
        self.assertEqual(result.rc, 0)
        self.assertIn("alpha", result.stdout)

    def test_current_window_commands_still_require_a_db_entry(self):
        for argv in (
            ["get-dir", "0"],
            ["open-files", "0"],
            ["reset-dir", "0"],
            ["swap-dirs", "0", "1"],
            ["delete-window"],
        ):
            with self.subTest(argv=argv):
                result = self._run_cli(["tmux-path-store", *argv], db=self._seed(), tmux_window="ghost")
                self.assertEqual(result.rc, 1)
                self.assertIn("not in the db", result.stderr)

    def test_delete_window_with_explicit_target_works_when_current_window_is_absent(self):
        result = self._run_cli(
            ["tmux-path-store", "delete-window", "beta"], db=self._seed(), tmux_window="ghost"
        )
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertNotIn("beta", self._read_windows())

    def test_delete_window_defaults_to_current_window(self):
        result = self._run_cli(["tmux-path-store", "delete-window"], db=self._seed())
        self.assertEqual((result.rc, result.stderr), (0, ""))
        db = self._read_windows()
        self.assertNotIn("alpha", db)
        self.assertIn("beta", db)

    def test_unknown_subcommand_is_reported_before_the_window_gate(self):
        result = self._run_cli(["tmux-path-store", "bogus-cmd"], db=self._seed(), tmux_window="ghost")
        self.assertEqual(result.rc, 1)
        self.assertIn("Unrecognized sub-command", result.stderr)
        self.assertNotIn("not in the db", result.stderr)

    # ------------------------------------------------------- editor handling

    def test_open_files_warns_with_the_actual_missing_index(self):
        db = self._seed()
        db["alpha"]["files"]["2"] = "/tmp/c"
        result = self._run_cli(
            ["tmux-path-store", "open-files", "0", "1", "2"], db=db, env_extra={"EDITOR": "ed --wait"}
        )
        self.assertEqual(result.rc, 0)
        self.assertIn("File 1 is not defined", result.stderr)
        self.assertNotIn("File 0 is not defined", result.stderr)
        result.run_mock.assert_called_with(["ed", "--wait", "/tmp/f0", "/tmp/c"], check=False)

    def test_open_files_with_no_defined_files_fails(self):
        result = self._run_cli(["tmux-path-store", "open-files", "1", "2"], db=self._seed())
        self.assertEqual(result.rc, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("No files are defined", result.stderr)

    def test_open_files_defaults_to_vi_when_editor_is_unset(self):
        result = self._run_cli(["tmux-path-store", "open-files", "0"], db=self._seed())
        self.assertEqual(result.rc, 0)
        result.run_mock.assert_called_with(["vi", "/tmp/f0"], check=False)

    def test_open_files_preserves_editor_exit_status(self):
        result = self._run_cli(
            ["tmux-path-store", "open-files", "0"],
            db=self._seed(),
            run_side_effect=[SimpleNamespace(stdout=f"{os.getpid()}\n", returncode=7)],
        )
        self.assertEqual(result.rc, 7)

    def test_get_file_reports_file_not_directory(self):
        result = self._run_cli(["tmux-path-store", "get-file", "1"], db=self._seed())
        self.assertEqual(result.rc, 1)
        self.assertIn("File 1 is not defined", result.stderr)

    # ------------------------------------------------ writes: atomicity/locking

    def test_save_failure_leaves_db_and_no_temp_files(self):
        db = self._seed()
        before = self._write_db(db).read_bytes()
        with mock.patch.object(module.os, "replace", side_effect=OSError("disk full")):
            result = self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/new"], db=db)
        self.assertEqual(result.rc, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("disk full", result.stderr)
        self.assertEqual(self._db_file().read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self._state_dir().iterdir()), ["db.json", "db.lock"])

    def test_successful_write_is_private(self):
        result = self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/new"], db=self._seed())
        self.assertEqual((result.rc, result.stderr), (0, ""))
        self.assertEqual(self._state_dir().stat().st_mode & 0o777, 0o700)
        self.assertEqual(self._db_file().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._read_windows()["alpha"]["dirs"]["1"]["path"], "/tmp/new")

    def test_writers_lock_exclusively_readers_share(self):
        with mock.patch.object(module.fcntl, "flock") as flock_mock:
            self._run_cli(["tmux-path-store", "set-dir", "1", "/tmp/new"], db=self._seed())
        self.assertIn(mock.call(mock.ANY, module.fcntl.LOCK_EX), flock_mock.call_args_list)
        self.assertIn(mock.call(mock.ANY, module.fcntl.LOCK_UN), flock_mock.call_args_list)
        with mock.patch.object(module.fcntl, "flock") as flock_mock:
            self._run_cli(["tmux-path-store", "list-windows"], db=self._seed())
        self.assertIn(mock.call(mock.ANY, module.fcntl.LOCK_SH), flock_mock.call_args_list)

    # ---------------------------------------------------- slot data operations

    def test_set_dir_defaults_to_cwd(self):
        work = self._workdir("tps-cwd-")
        result = self._run_cli(["tmux-path-store", "set-dir", "3"], db=self._seed(), chdir=work)
        self.assertEqual(result.rc, 0)
        self.assertEqual(self._read_windows()["alpha"]["dirs"]["3"]["path"], os.path.realpath(work))

    def test_set_dir_in_new_window_initialises_entry(self):
        result = self._run_cli(
            ["tmux-path-store", "set-dir", "0", "/tmp/x"], db=self._seed(), tmux_window="gamma"
        )
        self.assertEqual(result.rc, 0)
        db = self._read_windows()
        self.assertEqual(db["gamma"]["dirs"]["0"]["path"], "/tmp/x")
        self.assertEqual(db["gamma"]["files"], {})

    def test_set_file_stores_the_given_path_without_resolving(self):
        work = Path(self._workdir("tps-files-"))
        target = work / "target.txt"
        target.write_text("hi", encoding="utf-8")
        link = work / "link.txt"
        link.symlink_to(target)
        result = self._run_cli(["tmux-path-store", "set-file", "1", str(link)], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertEqual(self._read_windows()["alpha"]["files"]["1"]["path"], str(link))

    def test_set_file_stores_a_missing_file(self):
        work = Path(self._workdir("tps-missing-"))
        missing = work / "future.txt"
        result = self._run_cli(["tmux-path-store", "set-file", "1", str(missing)], db=self._seed())
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertEqual(self._read_windows()["alpha"]["files"]["1"]["path"], str(missing))

    def test_set_file_makes_a_relative_path_absolute(self):
        work = Path(self._workdir("tps-relative-"))
        result = self._run_cli(
            ["tmux-path-store", "set-file", "1", "future.txt"], db=self._seed(), chdir=work
        )
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertEqual(
            self._read_windows()["alpha"]["files"]["1"]["path"], os.path.join(work, "future.txt")
        )

    def test_open_files_warns_about_a_missing_file_and_still_opens_it(self):
        missing = str(Path(self._workdir("tps-open-")) / "gone.txt")
        db = {"alpha": {"dirs": {}, "files": {"0": {"path": missing}}}}
        result = self._run_cli(["tmux-path-store", "open-files", "0"], db=db)
        self.assertEqual(result.rc, 0, result.stderr)
        self.assertIn("does not exist", result.stderr)
        result.run_mock.assert_called_with(["vi", missing], check=False)

    def test_set_file_without_path_picks_most_recent_file(self):
        work = Path(self._workdir("tps-recent-"))
        older, newer = work / "older.txt", work / "newer.txt"
        older.write_text("old", encoding="utf-8")
        newer.write_text("new", encoding="utf-8")
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))
        with mock.patch.object(
            module.subprocess, "getoutput", side_effect=AssertionError("no shell-out expected")
        ):
            result = self._run_cli(["tmux-path-store", "set-file", "1"], db=self._seed(), chdir=work)
        self.assertEqual(result.rc, 0)
        self.assertEqual(self._read_windows()["alpha"]["files"]["1"]["path"], str(newer.resolve()))

    def test_set_file_in_empty_directory_reports_no_files(self):
        work = self._workdir("tps-empty-")
        result = self._run_cli(["tmux-path-store", "set-file", "1"], db=self._seed(), chdir=work)
        self.assertEqual(result.rc, 1)
        self.assertIn("no files", result.stderr.lower())

    def test_swap_and_reset_dirs(self):
        db = self._seed()
        db["alpha"]["dirs"]["1"] = {"path": "/tmp/a1"}
        result = self._run_cli(["tmux-path-store", "swap-dirs", "0", "1"], db=db)
        self.assertEqual(result.rc, 0)
        dirs = self._read_windows()["alpha"]["dirs"]
        self.assertEqual((dirs["0"]["path"], dirs["1"]["path"]), ("/tmp/a1", "/tmp/a0"))
        result = self._run_cli(["tmux-path-store", "reset-dir", "0"], db=self._read_windows())
        self.assertEqual(result.rc, 0)
        self.assertNotIn("0", self._read_windows()["alpha"]["dirs"])

    # --------------------------------------------------- window resolution

    def _fake_check_output(self, command, text):
        self.assertEqual(command, [str(self.python_exe), "display-message", "-p", "#W"])
        self.assertTrue(text)
        return "window\n"

    def test_window_command_uses_the_resolved_window(self):
        db = {"window": {"dirs": {"0": "/tmp/w0"}, "files": {}}}
        result = self._run_cli(
            ["tmux-path-store", "get-dir", "0"],
            db=db,
            tmux_window=None,
            check_output_side_effect=self._fake_check_output,
        )
        self.assertEqual((result.rc, result.stdout, result.stderr), (0, "/tmp/w0\n", ""))
        result.check_output_mock.assert_called_once()

    def test_window_command_reports_unresolvable_tmux(self):
        result = self._run_cli(
            ["tmux-path-store", "get-dir", "0"],
            db=self._seed(),
            tmux_window=None,
            pgrep_path=None,
        )
        self.assertEqual(result.rc, 1)
        self.assertIn("tmux", result.stderr)

    def test_missing_user_env_is_not_a_crash(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                module.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/pgrep" if name == "pgrep" else None,
            ),
            mock.patch.object(
                module.subprocess, "run", return_value=SimpleNamespace(stdout=f"{os.getpid()}\n")
            ),
        ):
            self.assertEqual(module.find_tmux_binary(), self.python_exe)

    # ------------------------------------------------------- entry points

    def test_version_uses_distribution_metadata(self):
        with mock.patch.object(module, "version", return_value="2026.10.1"):
            result = self._run_cli(["tmux-path-store", "--version"])
        self.assertEqual(result.stdout, "tmux-path-store 2026.10.1\n")
        self.assertEqual(result.rc, 0)

    def test_version_falls_back_when_running_from_source(self):
        error = module.PackageNotFoundError("tmux-path-store")
        with mock.patch.object(module, "version", side_effect=error):
            result = self._run_cli(["tmux-path-store", "--version"])
        self.assertEqual(result.rc, 0)
        self.assertIn("tmux-path-store", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_module_entry_point_runs(self):
        completed = subprocess.run(
            [sys.executable, "-m", "tmux_path_store", "help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("tmux-path-store aliases", completed.stdout)

    # ------------------------------------------------------- aliases / help

    def test_help_documents_ef(self):
        result = self._run_cli(["tmux-path-store", "help"])
        self.assertEqual(result.rc, 0)
        self.assertTrue(any(alias.startswith("ef") for _, alias, _ in module.g_help_rows))
        self.assertIn("ef", result.stdout)

    def test_posix_hooks_use_structured_paths_not_eval(self):
        for flag in ("--bash", "--zsh", "--sh"):
            with self.subTest(flag=flag):
                result = self._run_cli(["tmux-path-store", flag])
                self.assertNotIn("eval", result.stdout)
                self.assertIn("get-dir", result.stdout)

    def test_generated_aliases_cover_the_documented_surface(self):
        names = ("ld", "lf", "lda", "lfa", "lw", "dw", "cdh", "shd", "sdh", "swd", "swf", "tps", "ef")
        names += tuple(f"{prefix}{index}" for index in range(10) for prefix in ("cd", "sd", "sf", "rd", "rf", "f"))
        for flag in module.shells.SHELL_FLAGS:
            result = self._run_cli(["tmux-path-store", flag])
            self.assertEqual(result.rc, 0, result.stderr)
            for name in names:
                with self.subTest(flag=flag, name=name):
                    self.assertIn(name, result.stdout)


if __name__ == "__main__":
    unittest.main()
