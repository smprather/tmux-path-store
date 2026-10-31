"""Load the native shell hooks kept in ``shell_templates``.

The hooks invoke the installed ``tmux-path-store`` command and never interpolate
stored paths. csh/tcsh navigation is the exception: the CLI writes a private,
self-deleting source file because csh cannot change directory from data safely.
"""

import contextlib
import os
import tempfile
from importlib import resources
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

_TEMPLATES = {
    "bash": "posix",
    "zsh": "posix",
    "sh": "posix",
    "csh": "csh",
    "fish": "fish",
    "nu": "nu",
    "pwsh": "pwsh",
    "xonsh": "xonsh",
}


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


def init_script(shell: str) -> str:
    template = resources.files("tmux_path_store").joinpath("shell_templates", f"{_TEMPLATES[shell]}.tmpl")
    return template.read_text(encoding="utf-8")
