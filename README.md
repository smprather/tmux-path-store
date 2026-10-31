<div align="center">

# ⛯ tmux-path-store

**Your project's directories and files, one short command away.**

[![CI](https://github.com/smprather/tmux-path-store/actions/workflows/ci.yml/badge.svg)](https://github.com/smprather/tmux-path-store/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tmux-path-store)](https://pypi.org/project/tmux-path-store/)
[![Python 3.14+](https://img.shields.io/badge/Python-3.14%2B-3776AB?logo=python&logoColor=white)](https://github.com/smprather/tmux-path-store/blob/main/pyproject.toml)

[Quick start](#quick-start) · [Shell setup](#shell-setup) · [Commands](#commands) · [Editors](#editors) · [Scripting](#storage-and-scripting) · [Development](#development)

</div>

Bookmark the directories and files you use for a project under its **tmux window
name**. Jump back with `cdh`, switch to a related directory with `cd1`, or open a
saved file with `f0`.

Built for engineering workflows where project files, reports, and shared
resources live across the filesystem.

- **10 directory and 10 file slots** per window name, numbered `0`–`9`.
- **Shell shortcuts** for navigating, editing, listing, swapping, and clearing bookmarks.
- **Persistent local storage**, backed by Python's standard library with no runtime Python dependencies.

## Quick start

Use Linux or macOS with [tmux](https://github.com/tmux/tmux/wiki) on your `PATH`,
[uv](https://docs.astral.sh/uv/), and Python **3.14 or later**.

Install from PyPI:

```bash
uv tool install tmux-path-store
```

<details>
<summary><strong>Install from source or offline</strong></summary>

Install from a checkout:

```bash
git clone https://github.com/smprather/tmux-path-store.git
cd tmux-path-store
uv tool install .
```

Use `uv tool install --offline .` once Python and the build requirements are
available locally.

</details>

Enable shortcuts in your current Bash session; see [shell setup](#shell-setup)
for other shells and permanent configuration:

```bash
eval "$(tmux-path-store --bash)"
```

Inside tmux, give your window a project name and try these commands with your own
project paths:

```bash
tmux rename-window board-rev-a
cd /work/board-rev-a

shd                       # Save this project's home directory in slot 0
sd1 ./reports             # Save a related directory in slot 1
sf0 ./constraints.sdc      # Save an existing file in file slot 0

cd1                       # Jump to the reports directory
cdh                       # Return to the project's home directory
f0                        # Open the saved file in your editor
```

> [!IMPORTANT]
> Bookmarks are keyed by the exact window name, shared across tmux sessions.
> Reusing a name reuses its bookmarks. Renaming a window leaves its bookmarks
> under the previous name, so use stable, distinct project names.

## Shell setup

Add the matching initialization command to your shell's startup file, then reload
it or open a new shell.

| Shell               | Startup file                                                   | Initialization                     |
| ------------------- | -------------------------------------------------------------- | ---------------------------------- |
| Bash                | `~/.bashrc`                                                    | `eval "$(tmux-path-store --bash)"` |
| Zsh                 | `~/.zshrc`                                                     | `eval "$(tmux-path-store --zsh)"`  |
| sh, dash, ksh, mksh | `~/.profile` for login shells, or the file configured by `ENV` | `eval "$(tmux-path-store --sh)"`   |
| csh, tcsh           | `~/.cshrc` or `~/.tcshrc`                                      | `` eval `tmux-path-store --csh` `` |
| Fish                | `~/.config/fish/config.fish`                                   | `tmux-path-store --fish \| source` |

`--dash`, `--ksh`, and `--mksh` select the same hooks as `--sh`;
`--tcsh` is an alias for `--csh`.

<details>
<summary><strong>Nushell</strong></summary>

Generate a hook file from Nushell:

```nu
mkdir $nu.default-config-dir
let hook = ($nu.default-config-dir | path join 'tmux-path-store.nu')
tmux-path-store --nu | save --force $hook
print $"source '($hook)'"
```

Add the printed `source` line to the configuration file at `$nu.config-path`.
Nushell needs this literal file path because it loads definitions while parsing.
Regenerate the hook file after upgrading. `--nushell` also works.

</details>

<details>
<summary><strong>PowerShell on Unix</strong></summary>

Add this line to your startup file at `$PROFILE`:

```powershell
tmux-path-store --pwsh | Out-String | Invoke-Expression
```

`--powershell` also works. The tool requires tmux and Unix file locking, so native
Windows is not supported.

</details>

<details>
<summary><strong>Xonsh</strong></summary>

Add this line to `~/.xonshrc`:

```xonsh
exec($(tmux-path-store --xonsh))
```

</details>

> [!NOTE]
> PowerShell navigation rejects literal backslashes in Unix directory names
> because its filesystem provider treats them as separators
> ([upstream issue](https://github.com/PowerShell/PowerShell/issues/3666)).
> Nushell rejects control characters its native `cd` would remove, including
> carriage returns and escape characters; tabs and newlines are preserved.

## Commands

In the table below, replace `N` with a slot number from **0 to 9**.
Directory and file slots are independent.

| Shortcut                             | Action                                                                                              |
| ------------------------------------ | --------------------------------------------------------------------------------------------------- |
| `shd`, `sdh`                         | Save the current directory as the project home (`sd0`)                                              |
| `cdh`                                | Jump to the project home (`cd0`)                                                                    |
| `sdN [directory]`                    | Save a directory; defaults to the current directory                                                 |
| `sfN [file]`                         | Save an existing file; defaults to the most recently modified regular file in the current directory |
| `cdN`                                | Jump to directory slot N                                                                            |
| `fN`                                 | Open file slot N in your editor                                                                     |
| `ef <index> [index ...]`             | Open multiple file slots, for example `ef 0 2`                                                      |
| `ld [window ...]`, `lf [window ...]` | List directories or files for the current or named windows                                          |
| `lda`, `lfa`                         | List directories or files for all saved windows                                                     |
| `lw`                                 | List window names with saved bookmarks                                                              |
| `rdN`, `rfN`                         | Clear a directory or file slot                                                                      |
| `swd <a> <b>`, `swf <a> <b>`         | Swap two directory or file slots                                                                    |
| `dw [window ...]`                    | Remove saved bookmarks for named windows; defaults to the current window                            |
| `tps`                                | Show shortcut help (`tmux-path-store --help`)                                                       |

Directory arguments are made absolute when saved. File arguments resolve to
existing regular files. Each new window name needs a saved directory or file
before you can navigate or edit its slots.

## Editors

File shortcuts use `EDITOR`, falling back to `vi`. For example, in Bash or Zsh:

```bash
export EDITOR='code --wait'
```

Arguments follow POSIX quoting rules in every shell. Quote executable paths
containing spaces inside the value, for example
`EDITOR='"/opt/My Editor/bin/editor" --wait'`. Shell operators and variable
references in the value are not evaluated.

`ef` warns about empty slots and opens the defined ones; it fails if none are
defined. Editor exit codes are preserved. In PowerShell, inspect `$LASTEXITCODE`.

## Storage and scripting

Bookmarks persist across tmux restarts in
`~/.local/state/tmux_path_store/db.json`. Writes are atomic, coordinated with
file locks, and use file permissions `0600`.

The CLI also works without the shortcuts:

```bash
tmux-path-store set-dir 0 /work/board-rev-a
tmux-path-store set-file 0 /work/board-rev-a/constraints.sdc
tmux-path-store get-dir --json 0
tmux-path-store get-file --null 0
tmux-path-store open-files 0
```

`get-dir` and `get-file` return newline-terminated paths by default; use `--json`
for a JSON string or `--null` for NUL-terminated output. Check the exit status
before consuming output; diagnostics go to stderr.

Set `TMUX_WINDOW` to select a saved window explicitly in scripts:

```bash
TMUX_WINDOW=board-rev-a tmux-path-store get-dir 0
```

Otherwise, `TMUX_PANE` identifies the invoking pane's window when available.
The compatibility commands `cd`, `edit-file`, and `edit-files` **emit POSIX shell
code**; `open-files` launches the editor directly.

## Development

```bash
uv sync --locked
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check tmux_path_store tests
uv run ty check tmux_path_store tests
```

Local integration tests exercise installed shells; CI requires all 12 supported
shell names. tmux tests use private sockets and clean up only their own sessions.
CLI and storage logic live in `tmux_path_store/__init__.py`;
shell hooks live in `tmux_path_store/shells.py`.

Versions and release tags use CalVer `YYYY.M.N`, with no `v` prefix or zero
padding. `M` is the month and `N` starts at 1 each month; for example,
`2026.10.3`, then `2026.11.1` at the next month's first release.
Run `tmux-path-store --version` to inspect the installed version.
