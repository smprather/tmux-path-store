"""Generate shell hooks; paths and editor arguments stay data at shell boundaries."""

import contextlib
import os
import shlex
import tempfile
from pathlib import Path

SHELL_FLAGS = {
    "--bash": "bash",
    "--zsh": "zsh",
    "--sh": "sh",
    "--dash": "sh",
    "--ksh": "sh",
    "--mksh": "sh",
    "--csh": "csh",
    "--tcsh": "csh",
    "--fish": "fish",
    "--nu": "nu",
    "--nushell": "nu",
    "--pwsh": "pwsh",
    "--powershell": "pwsh",
    "--xonsh": "xonsh",
}


def command_aliases() -> dict[str, list[str]]:
    commands = {
        "ld": ["list-dirs"],
        "lf": ["list-files"],
        "lda": ["list-dirs", "__all__"],
        "lfa": ["list-files", "__all__"],
        "lw": ["list-windows"],
        "dw": ["delete-window"],
        "shd": ["set-dir", "0"],
        "sdh": ["set-dir", "0"],
        "swd": ["swap-dirs"],
        "swf": ["swap-files"],
        "ef": ["open-files"],
        "tps": ["help"],
    }
    for index in range(10):
        for prefix, command in (
            ("sd", "set-dir"),
            ("sf", "set-file"),
            ("rd", "reset-dir"),
            ("rf", "reset-file"),
            ("f", "open-files"),
        ):
            commands[f"{prefix}{index}"] = [command, str(index)]
    return commands


def navigation_aliases() -> dict[str, int]:
    return {"cdh": 0, **{f"cd{index}": index for index in range(10)}}


def csh_quote(value: str) -> str:
    # csh expands history even inside single quotes, and requires escaped newlines.
    return "'" + value.replace("!", r"\!").replace("'", "'\\''").replace("\n", "\\\n") + "'"


def csh_cd_script(directory: str) -> str:
    """Return a private source file, removed by its first command before cd runs."""
    temporary_dir = tempfile.gettempdir()
    if "\n" in temporary_dir or "\r" in temporary_dir:
        temporary_dir = "/tmp"
    fd, filename = tempfile.mkstemp(prefix="tmux-path-store-cd-", suffix=".csh", dir=temporary_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"/bin/rm -f -- {csh_quote(filename)}\ncd {csh_quote(directory)}\n")
    except BaseException:
        with contextlib.suppress(OSError):
            Path(filename).unlink()
        raise
    return filename


def posix_init(script: str) -> str:
    executable = shlex.quote(script)
    lines = [
        f'_tps_eval() {{ _tps_code=$(command {executable} "$@") && eval "$_tps_code"; }}',
    ]
    for name, args in command_aliases().items():
        command = shlex.join([script, *args])
        if name == "ef":
            lines.append(f'ef() {{ command {command} "$@"; }}')
        else:
            lines.append(f"alias {name}={shlex.quote(command)}")
    for name, index in navigation_aliases().items():
        lines.append(f"alias {name}='_tps_eval cd {index}'")
    return "; ".join(lines) + "\n"


def csh_init(script: str) -> str:
    lines = [
        f"alias {name} {csh_quote(shlex.join([script, *args]))}" for name, args in command_aliases().items()
    ]
    for name, index in navigation_aliases().items():
        command = (
            f'set _tps_script = "`{script} csh-cd {index}`"; test "$status" = 0 && source "$_tps_script"'
        )
        lines.append(f"alias {name} {csh_quote(command)}")
    # csh's documented eval `...` startup form folds newlines into spaces.
    return "; ".join(lines) + "\n"


def fish_init(script: str) -> str:
    executable = shlex.quote(script)
    lines = [
        "function _tps_cd",
        f"    set -l directory (command {executable} get-dir --null $argv | string split0)",
        "    set -l result $pipestatus[1]",
        "    test $result -eq 0; or return $result",
        '    cd -- "$directory"',
        "end",
    ]
    for name, args in command_aliases().items():
        lines.extend([f"function {name}", f"    command {shlex.join([script, *args])} $argv", "end"])
    for name, index in navigation_aliases().items():
        lines.extend([f"function {name}", f"    _tps_cd {index}", "end"])
    return "\n".join(lines) + "\n"


def nu_init(script: str) -> str:
    executable = "'" + script.replace("'", "''") + "'"
    lines = [
        "def --env _tps_cd [index: int] {",
        f"    let result = (^{executable} get-dir --json $index | complete)",
        "    if $result.exit_code != 0 { error make {msg: $result.stderr} }",
        "    let directory = ($result.stdout | from json)",
        r"    if $directory =~ '[\x00-\x08\x0b-\x1f]' {",
        "        error make {msg: 'Nushell cannot navigate names containing these control characters'}",
        "    }",
        # Nushell trims trailing whitespace from cd's argument. '/.' preserves it.
        "    cd ($directory + '/.')",
        "}",
    ]
    for name, args in command_aliases().items():
        fixed = " ".join("'" + arg + "'" for arg in args)
        lines.append(f"def --wrapped {name} [...args: string] {{ ^{executable} {fixed} ...$args }}")
    for name, index in navigation_aliases().items():
        lines.append(f"def --env {name} [] {{ _tps_cd {index} }}")
    return "\n".join(lines) + "\n"


def pwsh_init(script: str) -> str:
    executable = "'" + script.replace("'", "''") + "'"
    lines = [
        "function global:_tps_cd($Slot) {",
        f"    $directory = & {executable} get-dir --json $Slot",
        "    if ($LASTEXITCODE -ne 0) { throw 'tmux-path-store could not retrieve the directory' }",
        "    $directory = $directory | ConvertFrom-Json",
        "    if ($directory.Contains([char]92)) { throw 'PowerShell cannot navigate Unix names containing a literal backslash' }",
        "    Set-Location -LiteralPath $directory -ErrorAction Stop",
        "}",
    ]
    for name, args in command_aliases().items():
        fixed = " ".join("'" + arg + "'" for arg in args)
        lines.append(f"function global:{name} {{ & {executable} {fixed} @args }}")
    for name, index in navigation_aliases().items():
        lines.append(f"function global:{name} {{ _tps_cd {index} }}")
    return "\n".join(lines) + "\n"


def xonsh_init(script: str) -> str:
    lines = [
        "import json as _tps_json",
        "import subprocess as _tps_subprocess",
        "from xonsh.dirstack import cd as _tps_builtin_cd",
        "def _tps_navigation(index):",
        "    def invoke(args):",
        "        try:",
        "            result = _tps_subprocess.run(",
        f"                [{script!r}, 'get-dir', '--json', str(index)],",
        "                env=__xonsh__.env.detype(), capture_output=True, text=True)",
        "        except OSError as exc:",
        "            return None, str(exc), 127",
        "        if result.returncode:",
        "            return None, result.stderr, result.returncode",
        "        return _tps_builtin_cd([_tps_json.loads(result.stdout)])",
        "    return invoke",
    ]
    for name, args in command_aliases().items():
        lines.append(f"aliases[{name!r}] = {[script, *args]!r}")
    for name, index in navigation_aliases().items():
        lines.append(f"aliases[{name!r}] = _tps_navigation({index})")
    return "\n".join(lines) + "\n"


def init_script(shell: str, script: str) -> str:
    if shell in ("bash", "zsh", "sh"):
        return posix_init(script)
    return {"csh": csh_init, "fish": fish_init, "nu": nu_init, "pwsh": pwsh_init, "xonsh": xonsh_init}[shell](
        script
    )
