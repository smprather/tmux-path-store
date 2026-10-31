import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELLS = ("bash", "sh", "dash", "ksh", "mksh", "zsh", "fish", "csh", "tcsh", "nu", "pwsh", "xonsh")


class ShellIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = os.environ.get("TPS_REQUIRED_SHELLS", "").replace(",", " ").split()
        missing = [name for name in required if shutil.which(name) is None]
        if missing:
            raise RuntimeError(f"Required test shells are missing: {', '.join(missing)}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tps-shell-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.launcher = self.bin_dir / "tmux-path-store"
        self.launcher.write_text(
            f"#!{sys.executable}\nfrom tmux_path_store import cli\ncli()\n", encoding="utf-8"
        )
        self.launcher.chmod(0o700)
        self.editor = self.bin_dir / "test editor"
        self.editor.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "with open(os.environ['TPS_EDITOR_LOG'], 'w') as stream:\n"
            "    json.dump(sys.argv[1:], stream)\n"
            "sys.exit(int(os.environ.get('TPS_EDITOR_EXIT', '0')))\n",
            encoding="utf-8",
        )
        self.editor.chmod(0o700)

    def run_shell(
        self,
        shell,
        body,
        *,
        directory="work area",
        env_extra=None,
        launcher_failure=None,
        repeat_init=False,
        startup="file",
    ):
        executable = shutil.which(shell)
        if executable is None:
            self.skipTest(f"{shell} is not installed")
        home = self.root / shell
        home.mkdir(exist_ok=True)
        target = home / directory
        target.mkdir(exist_ok=True)
        (home / "tmp").mkdir(exist_ok=True)
        (home / "secondary area").mkdir(exist_ok=True)
        (home / "second file.txt").write_text("second\n", encoding="utf-8")
        file = target / "input file.txt"
        file.write_text("review\n", encoding="utf-8")
        state = home / ".local/state/tmux_path_store"
        state.mkdir(parents=True, exist_ok=True)
        (state / "db.json").write_text(
            json.dumps({"review": {"dirs": [str(target)] + [""] * 9, "files": [str(file)] + [""] * 9}}),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": os.pathsep.join(
                [
                    str(self.bin_dir),
                    *(
                        entry
                        for entry in os.environ.get("PATH", "").split(os.pathsep)
                        if not (Path(entry) / "tmux-path-store").exists()
                    ),
                ]
            ),
            "PYTHONPATH": str(REPO_ROOT),
            "TMUX_WINDOW": "review",
            "TPS_EDITOR_LOG": str(home / "editor.json"),
            "TMPDIR": str(home / "tmp"),
            "EDITOR": shlex.quote(str(self.editor)) + " --wait",
            **(env_extra or {}),
        }
        init = subprocess.run(
            [sys.executable, "-m", "tmux_path_store", "--" + shell],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        init_file = home / ("init.ps1" if shell == "pwsh" else "init")
        init_file.write_text(init.stdout, encoding="utf-8")
        if shell == "fish":
            command = [executable, "--no-config"]
            setup = f"source {shlex.quote(str(init_file))}\n"
        elif shell in ("csh", "tcsh"):
            command = [executable, "-f"]
            setup = f"source {shlex.quote(str(init_file))}\n"
        elif shell == "nu":
            command = [executable, "--no-config-file"]
            setup = f"source '{init_file}'\n"
        elif shell == "pwsh":
            command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File"]
            setup = f". '{init_file}'\n"
        elif shell == "xonsh":
            command = [executable, "--no-rc"]
            setup = f"exec(compile(open({str(init_file)!r}).read(), {str(init_file)!r}, 'exec'))\n"
        else:
            command = [executable, "-f"] if shell == "zsh" else [executable]
            setup = "shopt -s expand_aliases\n" if shell == "bash" else ""
            setup += f". {shlex.quote(str(init_file))}\n"
        if startup == "command":
            if shell in ("csh", "tcsh"):
                setup = f"eval `tmux-path-store --{shell}`\n"
            elif shell == "fish":
                setup = "tmux-path-store --fish | source\n"
            elif shell == "pwsh":
                setup = "tmux-path-store --pwsh | Out-String | Invoke-Expression\n"
            elif shell == "xonsh":
                setup = "exec($(tmux-path-store --xonsh))\n"
            elif shell != "nu":
                setup = "shopt -s expand_aliases\n" if shell == "bash" else ""
                setup += f'eval "$(tmux-path-store --{shell})"\n'
        elif startup == "legacy-bash":
            setup = "shopt -s expand_aliases\neval $(tmux-path-store --bash)\n"
        script = home / ("run.ps1" if shell == "pwsh" else "run")
        script.write_text(setup * (2 if repeat_init else 1) + body + "\n", encoding="utf-8")
        original = self.launcher.read_text(encoding="utf-8")
        if launcher_failure == "missing":
            self.launcher.unlink()
        elif launcher_failure is not None:
            self.launcher.write_text(f"#!/bin/sh\nexit {launcher_failure}\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [*command, str(script)],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            self.launcher.write_text(original, encoding="utf-8")
            self.launcher.chmod(0o700)
        self.assertEqual(list((home / "tmp").glob("tmux-path-store-cd-*.csh")), [])
        return result, target, file, home

    def test_navigation_preserves_special_paths(self):
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            for directory in (
                "work area",
                "bang!quote'cash$`tick`",
                "back\\slash",
                "back\\!bang",
                "quote\\'end",
                'double"quoted',
                "back\\\nnewline",
                "trailing\\",
                "hash#semi;star*bracket[]",
                "unicode-é漢字",
                "embedded\nnewline",
                "trailing\n\n",
            ):
                if shell == "pwsh" and "\\" in directory:
                    # PowerShell's provider normalizes both slashes, even with -LiteralPath.
                    # The separate rejection test checks that this cannot enter the wrong directory.
                    continue
                with self.subTest(shell=shell, directory=directory):
                    body = "cd0\npwd" if shell != "pwsh" else "cd0\n(Get-Location).Path"
                    result, target, _, _ = self.run_shell(shell, body, directory=directory)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, str(target) + "\n", result.stderr)

    def test_editor_flags_and_paths_reach_editor_unchanged(self):
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                result, _, file, home = self.run_shell(shell, "ef 0 1", directory="bang!quote'\nnewline")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((home / "editor.json").read_text()), ["--wait", str(file)])
                self.assertIn("File 1 is not defined", result.stderr)

    def test_documented_startup_commands(self):
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                body = "cd0\npwd" if shell != "pwsh" else "cd0\n(Get-Location).Path"
                result, target, _, _ = self.run_shell(shell, body, startup="command")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, str(target) + "\n", result.stderr)

    def test_original_unquoted_bash_startup_keeps_working(self):
        result, target, _, _ = self.run_shell("bash", "cd0\npwd", startup="legacy-bash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(target) + "\n")

    def test_navigation_propagates_cli_failures(self):
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            body = "cd0"
            if shell == "pwsh":
                body = "try { cd0; exit 0 } catch { exit 1 }"
            elif shell == "xonsh":
                body = "result = !(cd0)\nraise SystemExit(result.returncode)"
            for failure in (7, "missing"):
                with self.subTest(shell=shell, failure=failure):
                    result, _, _, _ = self.run_shell(shell, body, launcher_failure=failure)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_editor_failure_reaches_caller(self):
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            body = "f0" if shell != "pwsh" else "f0; exit $LASTEXITCODE"
            if shell == "xonsh":
                body = "result = !(f0)\nraise SystemExit(result.returncode)"
            with self.subTest(shell=shell):
                result, _, _, _ = self.run_shell(shell, body, env_extra={"TPS_EDITOR_EXIT": "7"})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_store_and_manage_aliases_after_reinitialization(self):
        body = (
            "shd\nsdh\nsd1 'secondary area'\nsf0 'second file.txt'\nsf1 'second file.txt'\n"
            "swd 0 1\nswf 0 1\nrd1\nrf1\ncdh\nld\nlf\nlda\nlfa\nlw\ndw\nlw"
        )
        for shell in SHELLS:
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                result, _, _, home = self.run_shell(shell, body, repeat_init=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(str(home / "secondary area"), result.stdout)
                self.assertIn(str(home / "second file.txt"), result.stdout)
                self.assertEqual(json.loads((home / ".local/state/tmux_path_store/db.json").read_text()), {"version": 2, "windows": {}})

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_powershell_rejects_literal_backslash_without_entering_a_different_directory(self):
        body = (
            "New-Item -ItemType Directory -Path './back/slash' -Force | Out-Null\n"
            "try { cd0; exit 0 } catch { (Get-Location).Path; exit 1 }"
        )
        result, _, _, home = self.run_shell("pwsh", body, directory="back\\slash")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(home) + "\n")

    @unittest.skipUnless(shutil.which("nu"), "Nushell is not installed")
    def test_nushell_rejects_controls_without_entering_a_different_directory(self):
        body = (
            "mkdir embeddedcarriage ESCame bell\n"
            "try { cd0; print 'accepted' } catch { print 'rejected' }\npwd"
        )
        for directory in ("embedded\rcarriage", "ESC\x1bname", "\x07bell"):
            with self.subTest(directory=directory):
                result, _, _, home = self.run_shell("nu", body, directory=directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "rejected\n" + str(home) + "\n")

    @unittest.skipUnless(shutil.which("xonsh"), "Xonsh is not installed")
    def test_xonsh_alias_output_supports_capture_pipes_and_redirection(self):
        body = (
            "listing = $(ld)\nprint('CAPTURE_BEGIN')\nprint(listing, end='')\n"
            "count = $(ld | wc -l)\nprint('LINES:' + count.strip())\nld > listing.txt"
        )
        result, _, _, home = self.run_shell("xonsh", body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("CAPTURE_BEGIN\nWindow: review\n"), result.stdout)
        self.assertIn("LINES:11\n", result.stdout)
        self.assertTrue((home / "listing.txt").read_text().startswith("Window: review\n"))


if __name__ == "__main__":
    unittest.main()
