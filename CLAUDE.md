# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tmux-path-store is a single-file Python CLI tool (`tmux-path-store`) that uses tmux window names as keys to store/retrieve directory paths and file paths. It stores data in `~/.local/state/tmux_path_store/db.json` using Python's built-in json module. Users add the matching eval command to their shell startup file to get shell aliases (cd0-cd9, sd0-sd9, sf0-sf9, cdh, shd, ld, lf, etc.): `--bash` for bash, `--zsh` for zsh, and `--csh`/`--tcsh` for csh/tcsh.

## Architecture

The entire tool lives in the `tmux_path_store` package with a `cli()` entry point exported as the `tmux-path-store` console script (`python -m tmux_path_store` works too, via `__main__.py`). It outputs shell commands to stdout for `eval` by the calling shell (e.g., `cd /some/path`), so errors/warnings go to stderr via `eprint()`/`wprint()`. The `--bash`, `--zsh`, and `--csh`/`--tcsh` flags generate shell-specific alias definitions. The db is a dict keyed by window name, each containing `dirs` and `files` arrays of 10 strings. Sub-commands dispatch through `dispatch()`, which returns True when the db must be written; writes are atomic (temp file + `os.replace`, mode 0600) and serialized with `flock` on `db.lock`, and entries are normalized (padded/coerced) on load so a drifted db cannot crash a command.

## Development

- **Python**: >=3.14, runtime deps: none
- **Linting**: `ruff` (line-length=110, target py314) and `ty check tmux_path_store tests`
- **Tests**: `python -m unittest discover -s tests -p 'test_*.py'` and `ruff check tmux_path_store tests`
- **CI**: `.github/workflows/ci.yml` runs unit tests, ruff, and ty on pushes and pull requests
- **Packaging**: `uv tool install .` or `uv tool install --offline .`
- **Lock file**: `uv.lock` is committed — deps are pinned for reproducible installs

## Memory Files

Before committing, update `~/.claude/projects/-home-ctr-mprather-dev-tmux-path-store/memory/` to reflect any behavioral or architectural changes. The memory files are the cold-start context for future agents — keep them in sync so agents don't operate on stale assumptions.

## Key Conventions

- stdout is reserved for shell-eval output; all user-facing messages use stderr via stdlib printing
- `eprint()` prints an error to stderr, prints `false` (with newline) to stdout, and exits 1 — so both eval'd and plain aliases fail correctly
- `parse_index()` rejects missing, non-integer, negative, and out-of-range slot numbers before any db access
- `ford` variable means "files or dirs" — used throughout to index into the db
- Releases use CalVer tags of the form `vYYYY.M.N`, where `N` is a 1-based counter that restarts at 1 on the first release of each month; `pyproject.toml` holds the version and `uv lock` keeps `uv.lock` in sync
