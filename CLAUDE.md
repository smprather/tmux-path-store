# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tmux-path-store is a single-file Python CLI tool (`tmux_path_store`) that uses tmux window names as keys to store/retrieve directory paths and file paths. It stores data in `~/.local/state/tmux_path_store/db.json` using orjson. Users add `eval $(tmux_path_store --bash)` to `.bashrc` to get shell aliases (cd0-cd9, sd0-sd9, sf0-sf9, cdh, shd, ld, lf, etc.).

## Architecture

The entire tool is a single executable Python script `tmux_path_store` (no `.py` extension) with a `cli()` entry point. It outputs shell commands to stdout for `eval` by the calling shell (e.g., `cd /some/path`), so errors/warnings go to stderr via `eprint()`/`wprint()`. The `--bash` flag generates alias definitions. The db is a dict keyed by window name, each containing `dirs` and `files` arrays of 10 strings.

## Development

- **Python**: >=3.14, runtime deps: `orjson`, `rich`
- **Linting**: `ruff` (line-length=110, target py314)
- **No test suite or build step** — run directly: `./tmux_path_store <sub-command>`
- **Lock file**: `uv.lock` is committed — deps are pinned for reproducible installs

## Memory Files

Before committing, update `~/.claude/projects/-home-ctr-mprather-dev-tmux-path-store/memory/` to reflect any behavioral or architectural changes. The memory files are the cold-start context for future agents — keep them in sync so agents don't operate on stale assumptions.

## Key Conventions

- stdout is reserved for shell-eval output; all user-facing messages use stderr via rich
- `eprint()` prints error to stderr and outputs `false` to stdout (so eval produces non-zero rc)
- `ford` variable means "files or dirs" — used throughout to index into the db
