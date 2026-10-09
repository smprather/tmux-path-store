#!/usr/bin/env python3
import contextlib
import fcntl
import getpass
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NoReturn

MIN_PYTHON = (3, 14)
if sys.version_info < MIN_PYTHON:
    sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or later is required.\n")

DISTRIBUTION_NAME = "tmux-path-store"
STATE_DIR_NAME = "tmux_path_store"
FORD_KEYS = ("dirs", "files")
SLOTS = 10
LISTING_SUB_CMDS = ("list-dirs", "list-files")
WRITE_SUB_CMDS = frozenset(
    {"set-dir", "set-file", "reset-dir", "reset-file", "delete-window", "swap-dirs", "swap-files"}
)
KNOWN_SUB_CMDS = frozenset(
    {
        "cd",
        "edit-file",
        "edit-files",
        *LISTING_SUB_CMDS,
        "set-dir",
        "set-file",
        "reset-dir",
        "reset-file",
        "get-dir",
        "get-file",
        "list-windows",
        "delete-window",
        "swap-dirs",
        "swap-files",
    }
)
FORD_BY_SUB_CMD = {
    "cd": "dirs",
    "list-dirs": "dirs",
    "set-dir": "dirs",
    "reset-dir": "dirs",
    "get-dir": "dirs",
    "swap-dirs": "dirs",
    "edit-file": "files",
    "edit-files": "files",
    "list-files": "files",
    "set-file": "files",
    "reset-file": "files",
    "get-file": "files",
    "swap-files": "files",
}

_SCRIPT_NAME_FALLBACKS = {"", "__init__", "__main__"}


def script_name(argv0: str) -> str:
    """Name to emit in generated aliases, falling back when started via an interpreter.

    Under ``python -m pkg`` sys.argv[0] is still "-m" while pkg/__init__ is imported,
    and it is "-c" for ``python -c``; both must not leak into generated aliases.
    """
    stem = Path(argv0).stem
    if stem.startswith("-") or stem in _SCRIPT_NAME_FALLBACKS:
        return DISTRIBUTION_NAME
    return stem


g_script = script_name(sys.argv[0] if sys.argv else "")

g_help_rows = (
    ("Navigate", "cdh", "cd home directory (same as cd0)"),
    ("Navigate", "cd[0-9]", "cd to directory #"),
    ("Navigate", "f[0-9]", "Edit file # with $EDITOR"),
    ("Navigate", "ef <#> ...", "Edit file(s) # with $EDITOR"),
    ("Store", "shd, sdh", "Set home directory (same as sd0)"),
    ("Store", "sd[0-9] [dir]", "Set directory # to pwd, or optional dir"),
    ("Store", "sf[0-9] <file>", "Set file # to <file>"),
    ("Inspect", "ld, lf", "List directories/files for current window"),
    ("Inspect", "lda, lfa", "List directories/files for all windows"),
    ("Inspect", "lw", "List all windows"),
    ("Manage", "rd[0-9], rf[0-9]", "Reset file/directory #"),
    ("Manage", "dw [<window>...]", "Delete window store. Defaults to current window"),
    ("Manage", "swd, swf <#> <#>", "Swap dir/file index1 with index2"),
    ("Help", "tps", "Print this help"),
)


def eprint(msg: str) -> NoReturn:
    """Since we are normally having the stdout eval'd by the shell, error messages
    go to stderr and a `false` goes to stdout so the eval'ing shell gets a non-zero
    return code. Never returns.
    """
    print(f"{g_script}: Error: {msg}", file=sys.stderr)
    print("false")
    sys.exit(1)


def wprint(msg: str) -> None:
    """Print a non-fatal warning to stderr. The command still succeeds."""
    print(f"{g_script}: Warning: {msg}", file=sys.stderr)


def print_window_contents(db: dict[str, dict[str, list[str]]], window_name: str, ford: str) -> None:
    print(f"Window: {window_name}")
    for index in range(SLOTS):
        print(f"  {index}: {db[window_name][ford][index]}")


def print_help() -> None:
    mode_width = max(len("Mode"), *(len(mode) for mode, _, _ in g_help_rows))
    alias_width = max(len("Alias"), *(len(alias) for _, alias, _ in g_help_rows))

    print("tmux-path-store aliases")
    print()
    print(f"{'Mode'.ljust(mode_width)}  {'Alias'.ljust(alias_width)}  Action")
    print(f"{'-' * mode_width}  {'-' * alias_width}  ------")
    for mode, alias, action in g_help_rows:
        print(f"{mode.ljust(mode_width)}  {alias.ljust(alias_width)}  {action}")
    print()
    print("posix shells:  eval \"$(tmux-path-store --bash)\"   or   --zsh")
    print("csh/tcsh:      eval `tmux-path-store --csh`")


def print_version() -> None:
    try:
        dist_version = version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        dist_version = "(unknown: running from a source checkout)"
    print(f"{DISTRIBUTION_NAME} {dist_version}")


def shell_words(*words: str) -> str:
    return " ".join(shlex.quote(word) for word in words)


def state_dir() -> Path:
    return Path(f"{os.environ['HOME']}/.local/state/{STATE_DIR_NAME}")


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def db_file_path() -> Path:
    return state_dir() / "db.json"


def tmux_server_binary_holder_path() -> Path:
    return state_dir() / "tmux_server_binary"


class DbError(Exception):
    """Raised when the on-disk db cannot be used safely."""


def normalize_slot_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    slots = [item if isinstance(item, str) else "" for item in items[:SLOTS]]
    return slots + [""] * (SLOTS - len(slots))


def normalize_db(raw: object) -> dict[str, dict[str, list[str]]]:
    if not isinstance(raw, dict):
        raise DbError(f"expected a JSON object at the top level, found {type(raw).__name__}")
    db: dict[str, dict[str, list[str]]] = {}
    for window_name, entry in raw.items():
        fields = entry if isinstance(entry, dict) else {}
        db[str(window_name)] = {key: normalize_slot_list(fields.get(key)) for key in FORD_KEYS}
    return db


def load_db(path: Path) -> dict[str, dict[str, list[str]]]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise DbError(f"cannot read {path}: {exc}") from exc
    try:
        return normalize_db(raw)
    except DbError as exc:
        raise DbError(f"cannot use {path}: {exc}") from exc


def save_db(path: Path, db: dict[str, dict[str, list[str]]]) -> None:
    """Write the db atomically so a crash can never leave a truncated file."""
    payload = json.dumps(db, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


@contextlib.contextmanager
def db_lock(exclusive: bool):
    """Serialize writers (and keep readers off a db being rewritten) via flock."""
    path = state_dir() / "db.lock"
    ensure_private_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def cached_tmux_server_binary(holder: Path) -> Path | None:
    """Return the cached tmux binary if it is safe to run, else discard the cache.

    The cache lives in our private state dir; reject symlinks and foreign-owned
    files so a stale entry can never execute something unexpected.
    """
    try:
        holder_stat = holder.lstat()
    except OSError:
        return None
    if holder_stat.st_uid != os.getuid() or stat.S_ISLNK(holder_stat.st_mode):
        with contextlib.suppress(OSError):
            holder.unlink(missing_ok=True)
        return None
    try:
        candidate = Path(holder.read_text(encoding="utf-8").strip())
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    except OSError:
        return None
    with contextlib.suppress(OSError):
        holder.unlink(missing_ok=True)
    return None


def discover_tmux_server_binary() -> Path:
    user = getpass.getuser()
    holder = tmux_server_binary_holder_path()
    if (cached := cached_tmux_server_binary(holder)) is not None:
        return cached
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        eprint("pgrep was not found on PATH; cannot locate the tmux server process.")
    result = subprocess.run(
        [pgrep, "-f", "-u", user, "tmux$"], check=False, capture_output=True, text=True
    )
    pids = result.stdout.split()
    if not pids:
        eprint("We do not appear to be in a tmux session.")
    binary = Path(f"/proc/{pids[0]}/exe").resolve()
    if not binary.is_file():
        eprint(f"The tmux server process {pids[0]} no longer exists.")
    ensure_private_dir(holder.parent)
    try:
        holder.write_text(str(binary), encoding="utf-8")
        holder.chmod(0o600)
    except OSError as exc:
        eprint(f"Could not cache the tmux binary path in {holder}: {exc}")
    return binary


def resolve_window() -> str:
    if window := os.getenv("TMUX_WINDOW"):
        return window
    if os.getenv("TMUX"):
        # Already inside a tmux session; use tmux from PATH (avoids stale binary cache issues)
        tmux_cmd = "tmux"
    else:
        tmux_cmd = str(discover_tmux_server_binary())
    try:
        window = subprocess.check_output([tmux_cmd, "display-message", "-p", "#W"], text=True).rstrip()
    except (OSError, subprocess.CalledProcessError):
        eprint(
            f"There was a problem running {tmux_cmd} display-message -p '#W'.\n"
            "There is probably not a running tmux session on this host.\n"
            "Try pgrep -u $USER -a tmux."
        )
    if not window:
        eprint("tmux did not report a window name.")
    return window


def parse_index(raw: str, what: str = "index") -> int:
    try:
        value = int(raw)
    except ValueError:
        eprint(f"'{raw}' is not a valid {what}; expected an integer from 0 to {SLOTS - 1}.")
    if not 0 <= value < SLOTS:
        eprint(f"{what.capitalize()} {value} is out of range; expected 0 to {SLOTS - 1}.")
    return value


def require_args(args: list[str], minimum: int, maximum: int | None, usage: str) -> None:
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        eprint(f"Wrong number of arguments. Usage: {g_script} {usage}")


def editor_command() -> str:
    return "$EDITOR" if os.environ.get("EDITOR") else "vi"


def most_recent_file(directory: Path) -> Path | None:
    newest: Path | None = None
    newest_mtime = -1
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            mtime = entry.stat(follow_symlinks=False).st_mtime_ns
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = Path(entry.path), mtime
    return newest


def empty_entry() -> dict[str, list[str]]:
    return {key: [""] * SLOTS for key in FORD_KEYS}


def needs_current_window(sub_cmd: str, args: list[str]) -> bool:
    """Read-only listings that never touch the current window must not require a
    db entry for it; neither must window initialization or an explicit delete.
    """
    if sub_cmd in ("set-dir", "set-file", "list-windows"):
        return False
    return not (sub_cmd in (*LISTING_SUB_CMDS, "delete-window") and args)


def dispatch(sub_cmd: str, args: list[str], window: str, db: dict[str, dict[str, list[str]]], ford: str) -> bool:
    """Run the sub-command against db; return True when the db must be written."""
    if sub_cmd == "cd":
        require_args(args, 1, 1, "cd <index>")
        index = parse_index(args[0])
        dir_lookup = db[window][ford][index]
        if dir_lookup == "":
            eprint(f"Directory {args[0]} is not defined for window '{window}'.")
        print(shell_words("cd", dir_lookup), end="")

    elif sub_cmd == "edit-file":
        require_args(args, 1, 1, "edit-file <index>")
        index = parse_index(args[0])
        lookup = db[window]["files"][index]
        if lookup == "":
            eprint(f"File {args[0]} is not defined for window '{window}'.")
        print(f"{editor_command()} {shlex.quote(lookup)}", end="")

    elif sub_cmd == "edit-files":
        require_args(args, 1, None, "edit-files <index> [<index> ...]")
        files = []
        for index in (parse_index(arg) for arg in args):
            lookup = db[window]["files"][index]
            if lookup == "":
                wprint(f"File {index} is not defined for window '{window}'.")
            else:
                files.append(lookup)
        if not files:
            eprint(f"No files are defined for window '{window}' among: {' '.join(args)}.")
        print(editor_command() + " " + " ".join(shlex.quote(file) for file in files), end="")

    elif sub_cmd in LISTING_SUB_CMDS:
        if args == ["__all__"]:
            # Print the current window contents last, when it has an entry
            for window_name in sorted(name for name in db if name != window):
                print_window_contents(db, window_name, ford)
            if window in db:
                print_window_contents(db, window, ford)
        elif args:
            for window_name in args:
                if window_name in db:
                    print_window_contents(db, window_name, ford)
                else:
                    wprint(f"Window {window_name} has not been defined.")
        else:
            print_window_contents(db, window, ford)

    elif sub_cmd == "set-dir":
        require_args(args, 1, 2, "set-dir <index> [<dir>]")
        index = parse_index(args[0])
        db.setdefault(window, empty_entry())
        db[window][ford][index] = args[1] if len(args) > 1 else os.getcwd()
        return True

    elif sub_cmd == "set-file":
        require_args(args, 1, 2, "set-file <index> [<file>]")
        index = parse_index(args[0])
        # If no file is given, default to the most recently modified
        if len(args) == 1:
            newest = most_recent_file(Path.cwd())
            if newest is None:
                eprint("There are no files in this directory.")
            args = [args[0], str(newest)]
        if not os.path.exists(args[1]):
            eprint(f"File {args[1]} does not exist. Not storing.")
        realpath = Path(args[1]).resolve()
        if not realpath.is_file():
            eprint(f"'{args[1]}' (realpath: '{realpath}') is not a file. Not storing.")
        db.setdefault(window, empty_entry())
        db[window][ford][index] = str(realpath)
        return True

    elif sub_cmd in ("reset-dir", "reset-file"):
        require_args(args, 1, 1, f"{sub_cmd} <index>")
        index = parse_index(args[0])
        db[window][ford][index] = ""
        return True

    elif sub_cmd in ("get-dir", "get-file"):
        require_args(args, 1, 1, f"{sub_cmd} <index>")
        index = parse_index(args[0])
        lookup = db[window][ford][index]
        if lookup == "":
            kind = "File" if ford == "files" else "Directory"
            eprint(f"{kind} {index} is not defined for window '{window}'.")
        print(lookup)

    elif sub_cmd == "list-windows":
        print("Windows:")
        for window_name in sorted(db):
            print("  ", window_name)

    elif sub_cmd == "delete-window":
        deleted = False
        for window_name in args or [window]:
            if window_name in db:
                del db[window_name]
                deleted = True
            else:
                wprint(f"The window '{window_name}' is not in the db.")
        return deleted

    elif sub_cmd in ("swap-dirs", "swap-files"):
        require_args(args, 2, 2, f"{sub_cmd} <index> <index>")
        src, dest = (parse_index(arg) for arg in args)
        db[window][ford][src], db[window][ford][dest] = db[window][ford][dest], db[window][ford][src]
        return True

    return False


def print_posix_aliases(shell: str) -> None:
    commands = [
        f"alias ld='{g_script} list-dirs';",
        f"alias lf='{g_script} list-files';",
        f"alias lda='{g_script} list-dirs __all__';",
        f"alias lfa='{g_script} list-files __all__';",
        f"alias lw='{g_script} list-windows';",
        f"alias dw='{g_script} delete-window';",
        f"alias cdh='eval \"$({g_script} cd 0)\"';",
        f"alias shd='{g_script} set-dir 0';",
        f"alias sdh='{g_script} set-dir 0';",
        f"alias swd='{g_script} swap-dirs';",
        f"alias swf='{g_script} swap-files';",
        f'ef() {{ eval "$({g_script} edit-files "$@")"; }};',
        f"alias tps='{g_script} help';",
    ]

    for index in range(SLOTS):
        commands.extend(
            [
                f"alias cd{index}='eval \"$({g_script} cd {index})\"';",
                f"alias sd{index}='{g_script} set-dir {index}';",
                f"alias sf{index}='{g_script} set-file {index}';",
                f"alias rd{index}='{g_script} reset-dir {index}';",
                f"alias rf{index}='{g_script} reset-file {index}';",
                f"alias f{index}='eval \"$({g_script} edit-file {index})\"';",
            ]
        )

    if shell == "zsh":
        commands.append(f"compdef _gnu_generic tps={g_script} 2>/dev/null || true;")

    print(" ".join(commands))


def print_csh_aliases() -> None:
    commands = [
        f"alias ld '{g_script} list-dirs'",
        f"alias lf '{g_script} list-files'",
        f"alias lda '{g_script} list-dirs __all__'",
        f"alias lfa '{g_script} list-files __all__'",
        f"alias lw '{g_script} list-windows'",
        f"alias dw '{g_script} delete-window'",
        f"alias cdh 'eval \"`{g_script} cd 0`\"'",
        f"alias shd '{g_script} set-dir 0'",
        f"alias sdh '{g_script} set-dir 0'",
        f"alias swd '{g_script} swap-dirs'",
        f"alias swf '{g_script} swap-files'",
        f"alias ef 'eval \"`{g_script} edit-files \\!*`\"'",
        f"alias tps '{g_script} help'",
    ]

    for index in range(SLOTS):
        commands.extend(
            [
                f"alias cd{index} 'eval \"`{g_script} cd {index}`\"'",
                f"alias sd{index} '{g_script} set-dir {index}'",
                f"alias sf{index} '{g_script} set-file {index}'",
                f"alias rd{index} '{g_script} reset-dir {index}'",
                f"alias rf{index} '{g_script} reset-file {index}'",
                f"alias f{index} 'eval \"`{g_script} edit-file {index}`\"'",
            ]
        )

    print("; ".join(commands))


def cli() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ["-h", "--help", "help"]:
        print_help()
        sys.exit()

    if sys.argv[1] in ["--version", "version"]:
        print_version()
        sys.exit()

    # Add the matching eval line to your shell startup file to get these aliases.
    if sys.argv[1] == "--bash":
        print_posix_aliases("bash")
        sys.exit()

    if sys.argv[1] == "--zsh":
        print_posix_aliases("zsh")
        sys.exit()

    if sys.argv[1] in ["--csh", "--tcsh"]:
        print_csh_aliases()
        sys.exit()

    sub_cmd = sys.argv[1]
    args = sys.argv[2:]
    if sub_cmd not in KNOWN_SUB_CMDS:
        eprint(f"Unrecognized sub-command '{sub_cmd}'.")

    ford = FORD_BY_SUB_CMD.get(sub_cmd, "")
    window = resolve_window()
    path = db_file_path()
    try:
        with db_lock(sub_cmd in WRITE_SUB_CMDS):
            db = load_db(path)
            if needs_current_window(sub_cmd, args) and window not in db:
                eprint(
                    f"Window {window} is not in the db. Use set-dir/set-file to init this window.\n"
                    "You should have shd/sd/sf aliases to these sub-commands."
                )
            if dispatch(sub_cmd, args, window, db, ford):
                save_db(path, db)
    except DbError as exc:
        eprint(f"{exc}. The file was left unchanged; fix or move it aside and retry.")
    except OSError as exc:
        eprint(f"Could not update {path}: {exc}")


if __name__ == "__main__":
    cli()
