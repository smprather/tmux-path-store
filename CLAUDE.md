# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tmux-path-store is a single-file Python CLI tool (`tmux-path-store`) that uses tmux window names as keys to store/retrieve directory paths and file paths. It stores data in `~/.local/state/tmux_path_store/db.json` using Python's built-in json module. Users add the matching eval command to their shell startup file to get shell aliases (cd0-cd9, sd0-sd9, sf0-sf9, cdh, shd, ld, lf, etc.): `--bash` for bash, `--zsh` for zsh, and `--csh`/`--tcsh` for csh/tcsh.

## Architecture

The entire tool lives in the `tmux_path_store` package with a `cli()` entry point exported as the `tmux-path-store` console script. It outputs shell commands to stdout for `eval` by the calling shell (e.g., `cd /some/path`), so errors/warnings go to stderr via `eprint()`/`wprint()`. The `--bash`, `--zsh`, and `--csh`/`--tcsh` flags generate shell-specific alias definitions. The db is a dict keyed by window name, each containing `dirs` and `files` arrays of 10 strings.

## Development

- **Python**: >=3.14, runtime deps: none
- **Linting**: `ruff` (line-length=110, target py314)
- **Tests**: `python -m unittest discover -s tests -p 'test_*.py'` and `ruff check tmux_path_store tests`
- **Packaging**: `uv tool install .` or `uv tool install --offline .`
- **Lock file**: `uv.lock` is committed — deps are pinned for reproducible installs

## Memory Files

Before committing, update `~/.claude/projects/-home-ctr-mprather-dev-tmux-path-store/memory/` to reflect any behavioral or architectural changes. The memory files are the cold-start context for future agents — keep them in sync so agents don't operate on stale assumptions.

## Key Conventions

- stdout is reserved for shell-eval output; all user-facing messages use stderr via stdlib printing
- `eprint()` prints error to stderr and outputs `false` to stdout (so eval produces non-zero rc)
- `ford` variable means "files or dirs" — used throughout to index into the db
- Releases use CalVer tags of the form `vYYYY.M.D`
