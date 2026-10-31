#!/usr/bin/env python3
import contextlib
import fcntl
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NoReturn, NotRequired, TypedDict

from . import shells

MIN_PYTHON = (3, 11)
if sys.version_info < MIN_PYTHON:
    sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or later is required.\n")

DISTRIBUTION_NAME = "tmux-path-store"
STATE_DIR_NAME = "tmux_path_store"
DB_VERSION = 2
FORD_KEYS = ("dirs", "files")
SLOTS = 10


class SlotEntry(TypedDict):
    path: str
    saved_at: NotRequired[int]
    last_used: NotRequired[int]


SlotMap = dict[str, SlotEntry]
WindowEntry = dict[str, SlotMap]
Db = dict[str, WindowEntry]
LISTING_SUB_CMDS = ("list-dirs", "list-files")
WRITE_SUB_CMDS = frozenset(
    {
        "set-dir",
        "set-file",
        "reset-dir",
        "reset-file",
        "delete-window",
        "swap-dirs",
        "swap-files",
        "csh-cd",
        "get-dir",
        "get-file",
        "open-files",
    }
)
KNOWN_SUB_CMDS = frozenset(
    {
        "csh-cd",
        "open-files",
        *LISTING_SUB_CMDS,
        "set-dir",
        "set-file",
        "reset-dir",
        "reset-file",
        "get-dir",
        "get-file",
        "list-windows",
        "recent",
        "delete-window",
        "swap-dirs",
        "swap-files",
    }
)
FORD_BY_SUB_CMD = {
    "csh-cd": "dirs",
    "list-dirs": "dirs",
    "set-dir": "dirs",
    "reset-dir": "dirs",
    "get-dir": "dirs",
    "swap-dirs": "dirs",
    "open-files": "files",
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
    ("Inspect", "recent [n]", "List recently used dirs/files"),
    ("Manage", "rd[0-9], rf[0-9]", "Reset file/directory #"),
    ("Manage", "dw [<window>...]", "Delete window store. Defaults to current window"),
    ("Manage", "swd, swf <#> <#>", "Swap dir/file index1 with index2"),
    ("Help", "tps", "Print this help"),
)


def eprint(msg: str) -> NoReturn:
    """Report an error on stderr; callers must check the exit status. Never returns."""
    print(f"{g_script}: Error: {msg}", file=sys.stderr)
    sys.exit(1)


def wprint(msg: str) -> None:
    """Print a non-fatal warning to stderr. The command still succeeds."""
    print(f"{g_script}: Warning: {msg}", file=sys.stderr)


def print_window_contents(db: Db, window_name: str, ford: str) -> None:
    print(f"Window: {window_name}")
    slots = db[window_name][ford]
    for index in range(SLOTS):
        entry = slots.get(str(index))
        print(f"  {index}: {entry['path'] if entry else ''}")


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
    print('POSIX shells:  eval "$(tmux-path-store --bash)"   or --zsh/--sh/--dash/--ksh/--mksh')
    print("csh/tcsh:      eval `tmux-path-store --csh`")
    print("fish:          tmux-path-store --fish | source")
    print("Nushell:       save --nu output to a file and source it")
    print("PowerShell:    tmux-path-store --pwsh | Out-String | Invoke-Expression")
    print("Xonsh:         exec($(tmux-path-store --xonsh))")


def print_version() -> None:
    try:
        dist_version = version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        dist_version = "(unknown: running from a source checkout)"
    print(f"{DISTRIBUTION_NAME} {dist_version}")


def state_dir() -> Path:
    if xdg_state := os.environ.get("XDG_STATE_HOME"):
        candidate = Path(xdg_state)
        if candidate.is_absolute():
            return candidate / STATE_DIR_NAME
    home = os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / STATE_DIR_NAME


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def db_file_path() -> Path:
    return state_dir() / "db.json"


class DbError(Exception):
    """Raised when the on-disk db cannot be used safely."""


def normalize_slot_entry(value: object) -> SlotEntry | None:
    """Accept a v2 entry, a v1 path string, or a legacy list element."""
    if isinstance(value, str):
        return {"path": value} if value else None
    if not isinstance(value, dict):
        return None
    path = value.get("path")
    if not isinstance(path, str) or not path:
        return None
    entry: SlotEntry = {"path": path}
    saved_at = value.get("saved_at")
    if type(saved_at) is int:
        entry["saved_at"] = saved_at
    last_used = value.get("last_used")
    if type(last_used) is int:
        entry["last_used"] = last_used
    return entry


def normalize_slot_map(value: object) -> SlotMap:
    """Accept the sparse map or the legacy 10-element list; drop unusable slots."""
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return {}
    slots: SlotMap = {}
    for raw_slot, raw_entry in items:
        try:
            index = int(raw_slot)
        except (TypeError, ValueError):
            continue
        if not 0 <= index < SLOTS:
            continue
        entry = normalize_slot_entry(raw_entry)
        if entry is not None:
            slots[str(index)] = entry
    return slots


def normalize_entry(value: object) -> WindowEntry:
    fields: dict = value if isinstance(value, dict) else {}
    return {key: normalize_slot_map(fields.get(key)) for key in FORD_KEYS}


def normalize_db(raw: object) -> Db:
    if not isinstance(raw, dict):
        raise DbError(f"expected a JSON object at the top level, found {type(raw).__name__}")
    if type(raw.get("version")) is int:
        version = raw["version"]
        if version not in (1, DB_VERSION):
            raise DbError(f"unsupported database version {version}; expected {DB_VERSION}")
        windows = raw.get("windows")
        if not isinstance(windows, dict):
            raise DbError("expected a 'windows' object in the database")
    else:
        # Databases written before version 1 were a bare map of window entries.
        windows = raw
    return {str(name): normalize_entry(entry) for name, entry in windows.items()}


def load_db(path: Path) -> Db:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DbError(f"cannot read {path}: {exc}") from exc
    try:
        return normalize_db(raw)
    except DbError as exc:
        raise DbError(f"cannot use {path}: {exc}") from exc


def save_db(path: Path, db: Db) -> None:
    """Write the db atomically so a crash can never leave a truncated file."""
    payload = json.dumps({"version": DB_VERSION, "windows": db}, separators=(",", ":"))
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
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with contextlib.suppress(OSError):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def find_tmux_binary() -> Path | None:
    """Locate a tmux executable: server PID first, then PATH, then process lookup."""
    if tmux := os.getenv("TMUX"):
        fields = tmux.split(",")
        if len(fields) > 1 and fields[1].isdigit():
            candidate = Path(f"/proc/{fields[1]}/exe")
            if candidate.is_file():
                return candidate.resolve()
    if tmux_path := shutil.which("tmux"):
        return Path(tmux_path)
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return None
    result = subprocess.run(
        [pgrep, "-f", "-u", getpass.getuser(), r"(^|/)tmux([[:space:]:]|$)"],
        check=False,
        capture_output=True,
        text=True,
    )
    pids = result.stdout.split()
    if not pids:
        return None
    binary = Path(f"/proc/{pids[0]}/exe")
    return binary.resolve() if binary.is_file() else None


def optional_window() -> str | None:
    """Resolve the current window for ordering without failing a listing."""
    return os.getenv("TMUX_WINDOW") or query_window()


def query_window() -> str | None:
    """Return the invoking pane's window name, or None when tmux is unreachable."""
    binary = find_tmux_binary()
    if binary is None:
        return None
    command = [str(binary), "display-message", "-p"]
    if pane := os.getenv("TMUX_PANE"):
        command.extend(["-t", pane])
    command.append("#W")
    try:
        window = subprocess.check_output(command, text=True).removesuffix("\n")
    except (OSError, subprocess.CalledProcessError):
        return None
    return window or None


def resolve_window() -> str:
    if window := os.getenv("TMUX_WINDOW"):
        return window
    if window := query_window():
        return window
    eprint(
        "Could not determine the current tmux window. "
        "Is tmux running and on PATH, or set TMUX_WINDOW?"
    )


def parse_index(raw: str, what: str = "index") -> int:
    try:
        value = int(raw)
    except ValueError:
        eprint(f"'{raw}' is not a valid {what}; expected an integer from 0 to {SLOTS - 1}.")
    if not 0 <= value < SLOTS:
        eprint(f"{what.capitalize()} {value} is out of range; expected 0 to {SLOTS - 1}.")
    return value


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        eprint(f"'{raw}' is not a valid count; expected a non-negative integer.")
    if value < 0:
        eprint(f"Count {value} is out of range; expected a non-negative integer.")
    return value


def require_args(args: list[str], minimum: int, maximum: int | None, usage: str) -> None:
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        eprint(f"Wrong number of arguments. Usage: {g_script} {usage}")


def editor_arguments() -> list[str]:
    try:
        args = shlex.split(os.environ.get("EDITOR") or "vi")
    except ValueError as exc:
        eprint(f"Cannot parse EDITOR: {exc}")
    if not args:
        eprint("EDITOR does not contain a command.")
    return args


def selected_files(args: list[str], window: str, db: Db) -> list[str]:
    files = []
    for index in (parse_index(arg) for arg in args):
        entry = db[window]["files"].get(str(index))
        if entry is None:
            wprint(f"File {index} is not defined for window '{window}'.")
        else:
            if not os.path.exists(entry["path"]):
                wprint(f"File {index} does not exist; opening it anyway: {entry['path']}")
            entry["last_used"] = now_ns()
            files.append(entry["path"])
    if not files:
        eprint(f"No files are defined for window '{window}' among: {' '.join(args)}.")
    return files


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


def empty_entry() -> WindowEntry:
    return {key: {} for key in FORD_KEYS}


def now_ns() -> int:
    return time.time_ns()


def slot_path(db: Db, window: str, ford: str, index: int) -> str:
    entry = db[window][ford].get(str(index))
    return entry["path"] if entry else ""


def needs_window_key(sub_cmd: str, args: list[str]) -> bool:
    """Only commands that read or write a window's bookmarks need a window name."""
    if sub_cmd == "list-windows":
        return False
    if sub_cmd in LISTING_SUB_CMDS and args:
        return False
    return not (sub_cmd == "delete-window" and args)


def wants_optional_window(sub_cmd: str, args: list[str]) -> bool:
    """`__all__` puts the current window last when tmux can name it."""
    return sub_cmd in LISTING_SUB_CMDS and args == ["__all__"]


def needs_current_window(sub_cmd: str, args: list[str]) -> bool:
    """Read-only listings that never touch the current window must not require a
    db entry for it; neither must window initialization or an explicit delete.
    """
    if sub_cmd in ("set-dir", "set-file", "list-windows"):
        return False
    return not (sub_cmd in (*LISTING_SUB_CMDS, "delete-window") and args)


def dispatch(sub_cmd: str, args: list[str], window: str | None, db: Db, ford: str) -> bool:
    """Run the sub-command against db; return True when the db must be written."""
    if sub_cmd == "csh-cd":
        assert window is not None
        require_args(args, 1, 1, "csh-cd <index>")
        index = parse_index(args[0])
        entry = db[window][ford].get(str(index))
        if entry is None:
            eprint(f"Directory {args[0]} is not defined for window '{window}'.")
        entry["last_used"] = now_ns()
        print(shells.csh_cd_script(entry["path"]))
        return True

    elif sub_cmd in LISTING_SUB_CMDS:
        if args == ["__all__"]:
            # Print the current window contents last, when it has an entry
            for window_name in sorted(name for name in db if name != window):
                print_window_contents(db, window_name, ford)
            if window is not None and window in db:
                print_window_contents(db, window, ford)
        elif args:
            for window_name in args:
                if window_name in db:
                    print_window_contents(db, window_name, ford)
                else:
                    wprint(f"Window {window_name} has not been defined.")
        else:
            assert window is not None
            print_window_contents(db, window, ford)

    elif sub_cmd == "set-dir":
        assert window is not None
        require_args(args, 1, 2, "set-dir <index> [<dir>]")
        index = parse_index(args[0])
        path = os.path.abspath(args[1]) if len(args) > 1 else os.getcwd()
        db.setdefault(window, empty_entry())[ford][str(index)] = {"path": path, "saved_at": now_ns()}
        return True

    elif sub_cmd == "set-file":
        assert window is not None
        require_args(args, 1, 2, "set-file <index> [<file>]")
        index = parse_index(args[0])
        # If no file is given, default to the most recently modified
        if len(args) == 1:
            newest = most_recent_file(Path.cwd())
            if newest is None:
                eprint("There are no files in this directory.")
            args = [args[0], str(newest)]
        db.setdefault(window, empty_entry())[ford][str(index)] = {
            "path": os.path.abspath(args[1]),
            "saved_at": now_ns(),
        }
        return True

    elif sub_cmd in ("reset-dir", "reset-file"):
        assert window is not None
        require_args(args, 1, 1, f"{sub_cmd} <index>")
        index = parse_index(args[0])
        db[window][ford].pop(str(index), None)
        return True

    elif sub_cmd in ("get-dir", "get-file"):
        assert window is not None
        output_format = args[0] if args and args[0] in ("--json", "--null") else None
        if output_format:
            args = args[1:]
        require_args(args, 1, 1, f"{sub_cmd} <index>")
        index = parse_index(args[0])
        entry = db[window][ford].get(str(index))
        if entry is None:
            kind = "File" if ford == "files" else "Directory"
            eprint(f"{kind} {index} is not defined for window '{window}'.")
        entry["last_used"] = now_ns()
        lookup = entry["path"]
        if output_format == "--json":
            print(json.dumps(lookup))
        else:
            print(lookup, end="\0" if output_format == "--null" else "\n")
        return True

    elif sub_cmd == "recent":
        assert window is not None
        require_args(args, 0, 1, "recent [<count>]")
        count = parse_count(args[0]) if args else 2 * SLOTS
        recent: list[tuple[int, str, int, str]] = []
        for kind, key in (("dir", "dirs"), ("file", "files")):
            for raw_slot, entry in db[window][key].items():
                stamp = entry.get("last_used") or entry.get("saved_at") or 0
                recent.append((stamp, kind, int(raw_slot), entry["path"]))
        recent.sort(key=lambda item: (-item[0], item[1], item[2]))
        for _, kind, slot, path in recent[:count]:
            print(f"{kind} {slot} {path}")

    elif sub_cmd == "list-windows":
        print("Windows:")
        for window_name in sorted(db):
            print("  ", window_name)

    elif sub_cmd == "delete-window":
        deleted = False
        targets = args or ([window] if window is not None else [])
        for window_name in targets:
            if window_name in db:
                del db[window_name]
                deleted = True
            else:
                wprint(f"The window '{window_name}' is not in the db.")
        return deleted

    elif sub_cmd in ("swap-dirs", "swap-files"):
        assert window is not None
        require_args(args, 2, 2, f"{sub_cmd} <index> <index>")
        src, dest = (parse_index(arg) for arg in args)
        slots = db[window][ford]
        src_value = slots.pop(str(src), None)
        dest_value = slots.pop(str(dest), None)
        if dest_value is not None:
            slots[str(src)] = dest_value
        if src_value is not None:
            slots[str(dest)] = src_value
        return True

    return False


def cli() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ["-h", "--help", "help"]:
        print_help()
        sys.exit()

    if sys.argv[1] in ["--version", "version"]:
        print_version()
        sys.exit()

    # Add the matching eval line to your shell startup file to get these aliases.
    if shell := shells.SHELL_FLAGS.get(sys.argv[1]):
        print(shells.init_script(shell), end="")
        sys.exit()

    sub_cmd = sys.argv[1]
    args = sys.argv[2:]
    if sub_cmd not in KNOWN_SUB_CMDS:
        eprint(f"Unrecognized sub-command '{sub_cmd}'.")

    ford = FORD_BY_SUB_CMD.get(sub_cmd, "")
    window = resolve_window() if needs_window_key(sub_cmd, args) else None
    if window is None and wants_optional_window(sub_cmd, args):
        window = optional_window()
    path = db_file_path()
    try:
        editor_args = None
        with db_lock(sub_cmd in WRITE_SUB_CMDS):
            db = load_db(path)
            if window is not None and needs_current_window(sub_cmd, args) and window not in db:
                eprint(
                    f"Window {window} is not in the db. Use set-dir/set-file to init this window.\n"
                    "You should have shd/sd/sf aliases to these sub-commands."
                )
            if sub_cmd == "open-files":
                assert window is not None
                require_args(args, 1, None, "open-files <index> [<index> ...]")
                editor_args = editor_arguments() + selected_files(args, window, db)
                save_db(path, db)
            elif dispatch(sub_cmd, args, window, db, ford):
                save_db(path, db)
        if editor_args is not None:
            result = subprocess.run(editor_args, check=False)
            sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
    except DbError as exc:
        eprint(f"{exc}. The file was left unchanged; fix or move it aside and retry.")
    except OSError as exc:
        eprint(f"Could not update {path}: {exc}")


if __name__ == "__main__":
    cli()
