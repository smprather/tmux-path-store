# ⛯ tmux-path-store
Uses the [tmux](https://github.com/tmux/tmux/wiki) window name as an index into a list of stored directory paths and files.

Provides shell aliases to set/retrieve/manage the stored paths and files.

Designed to meet the needs of Electrical Engineers working on multiple projects/sub-projects simultaneously. We tend to have work directories and input/output
collateral dirs/files scattered all over our filesystem space. I have had 10+ different
work-products in flight at the same time, each with their own home-base directory and related directories. Necessity was the mother of this simple invention.

Maybe not as useful for pure software devs that are very "repo-centric". But I'm sure many other engineer and engineer-adjacent (dev-ops, etc) disciplines will
find this useful.

Enabled by querying the current tmux window name  
`tmux display-message -p '#W'"`

# ⛯ Aliases
### Add this to your `.bashrc`
`eval $(tmux_path_store --bash)`
### Which will creates these aliases
| Alias             | Action                                          |
| ----------------- | ----------------------------------------------- |
| shd, sdh          | Set home directory (same as sd0)                |
| cdh               | cd home directory (same as cd0)                 |
| f[0-9]            | Edit file # with your $EDITOR                   |
| sd[0-9] [dir]     | Set directory # to the pwd, or optional dir     |
| sf[0-9] <file>    | Set file # to <file>                            |
| rd[0-9], rf[0-9]  | Reset(delete) file/directory                    |
| cd[0-9]           | cd to directory #                               |
| ld, lf            | List directories/files for the current window   |
| lda, lfa          | List directories/files for all windows          |
| lw                | List all windows                                |
| dw [<window>...]  | Delete window(s) store. Default: Current window |
| swd, swf <#> <#>  | Swap dir/file index1 with index2                |
| tps               | Print this help                                 |

# ⛯ Examples
### Name you current tmux window
<kbd>tmux-leader</kbd><kbd>,</kbd> _phase1_ <kbd>ENTER</kbd>
### Define a home-dir and some related-dirs and files
```bash
### Closest thing to a home-dir for this project
$ cd /proj/collect_underpants/users/binklin

### "Set Home Directory", alias for 'tmux_path_store set-dir 0'
$ shd

### cd off into oblivion doing whatever...
$ cd /garden; do-day-job; cd /forest; pick-magic-shrooms; etc...

### Ok, time to get back to world-domination
### "cd Home", alias cdh='eval $(tmux_path_store cd 0)'; 
$ cdh
$ pwd
/proj/collect_underpants/users/binklin

### We spend a lot of time in Tweek's room though
$ cd /southpark/tweak_residence/tweeks_room
$ sd1
$ procure-underpants

### Back home to prepare for top-secret phase2
$ cdh
$ phase2-sync-meeting
### Decided we were way short of underpants
$ cd1
$ procure-underpants

### List directories, alias ld='tmux_path_store list-dirs'
$ ld
Window: phase1
  0: /proj/collect_underpants/users/binklin
  1: /southpark/tweak_residence/tweeks_room
  2:
  ....
  9:
```
### Create a tmux window to work on phase2 (security clearance required: SCI Top Secret)
<kbd>tmux-leader</kbd><kbd>c</kbd>  
<kbd>tmux-leader</kbd><kbd>,</kbd> <i>phase2</i><kbd>ENTER</kbd>
```bash
$ cd /proj/phase2/users/blinklin
$ shd
$ cd <redacted>; <redacted>
$ sd1
$ cdh
```
