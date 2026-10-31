# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tmux-path-store is a dependency-free Python CLI package that uses tmux window names as keys to store/retrieve directory paths and file paths. It stores data in `~/.local/state/tmux_path_store/db.json` using Python's built-in json module. Shell hooks provide shortcuts such as cd0-cd9, sd0-sd9, sf0-sf9, cdh, shd, ld, and lf. Supported platforms are Linux and macOS with tmux; Unix file locking and tmux exclude native Windows support.

## Architecture

The `tmux_path_store` package exports `cli()` as the `tmux-path-store` console script (`python -m tmux_path_store` works too, via `__main__.py`). CLI dispatch, tmux discovery, database handling, and editor execution live in `__init__.py`; native hook generation lives in `shells.py`.

Setup flags are `--bash`, `--zsh`, `--sh`/`--dash`/`--ksh`/`--mksh`, `--csh`/`--tcsh`, `--fish`, `--nu`/`--nushell`, `--pwsh`/`--powershell`, and `--xonsh`. POSIX hooks capture CLI output and check its status before evaluating it. csh/tcsh navigation uses `csh-cd`, which returns the path of a mode-0600 temporary source script; the script removes itself before changing directory, and the hook guards `source` with the CLI status. Fish reads NUL-terminated paths; Nushell, PowerShell, and Xonsh read JSON strings and invoke their native directory operation. Nushell hooks must be saved and sourced from a literal file path because definitions load at parse time. See README.md for the startup files and commands.

The compatibility commands `cd`, `edit-file`, and `edit-files` output POSIX shell code to stdout. The generated file shortcuts use `open-files`, which parses `EDITOR` with `shlex.split` and executes an argument list directly after releasing the database lock. It preserves the editor exit status and maps signals to `128 + signal`. Do not interpolate editor arguments or stored paths into an evaluated command. `get-dir` and `get-file` support `--json` and `--null` before the index for native hooks and scripts; their default is a newline-terminated path. Errors and warnings use stderr via `eprint()`/`wprint()`.

The db is a dict keyed by exact window name, each containing `dirs` and `files` arrays of 10 strings. `TMUX_WINDOW` overrides window lookup; otherwise `TMUX_PANE`, when present, targets the invoking pane rather than the active window. Keep `tmux` on PATH for portable discovery; the process lookup fallback uses Linux `/proc`. Relative directory inputs are normalized to absolute paths at store time; file inputs resolve to existing regular files. Sub-commands dispatch through `dispatch()`, which returns True when the db must be written; writes are atomic (temp file + `os.replace`, mode 0600) and serialized with `flock` on `db.lock`, and entries are normalized (padded/coerced) on load so a drifted db cannot crash a command.

## Development

- **Python**: >=3.14, runtime deps: none
- **Linting**: `ruff` (line-length=110, target py314) and `ty check tmux_path_store tests`
- **Tests**: `uv run python -m unittest discover -s tests -p 'test_*.py'`; discovery includes actual shell execution and private-socket tmux regression tests
- **CI**: `.github/workflows/ci.yml` runs unit/integration tests, ruff, and ty on pushes and pull requests. It requires Bash, sh, dash, ksh, mksh, Zsh, Fish, csh, tcsh, Nushell, PowerShell, and Xonsh via `TPS_REQUIRED_SHELLS`; `.github/scripts/install-test-shells.sh` provisions checksum-pinned Nu and Xonsh locally and a pinned PowerShell fallback when the runner has no `pwsh`
- **Packaging**: `uv tool install .` or `uv tool install --offline .`
- **Lock file**: `uv.lock` is committed — deps are pinned for reproducible installs

## Memory Files

Before committing, update `~/.claude/projects/-home-mylesp-tmux-path-store/memory/` to reflect any behavioral or architectural changes. The memory files are the cold-start context for future agents — keep them in sync so agents don't operate on stale assumptions.

## Key Conventions

- Shell command emitters write runnable code to stdout; data retrieval, listings, help, and version commands write their requested output. Diagnostics use stderr. `open-files` executes the editor rather than emitting code.
- `eprint()` prints an error to stderr, prints the legacy `false` marker (with newline) to stdout, and exits 1. Hooks must check the exit status before evaluating code or decoding paths.
- `parse_index()` rejects missing, non-integer, negative, and out-of-range slot numbers before any db access
- `ford` variable means "files or dirs" — used throughout to index into the db
- Versions and release tags use canonical CalVer `YYYY.M.N`, with no `v` prefix or zero padding, matching engineering-loadout. `M` is the month (1–12) and `N` is a 1-based counter that restarts at 1 each month; `pyproject.toml` holds the version and `uv lock` keeps `uv.lock` in sync
- Integration tests must use a private tmux socket and clean up only their own session; never kill the shared/default tmux server
