
"""rebaseplan

Usage:
  rebaseplan [show] [options] [(--pattern=<pat>)...] [(-- <log-options>...)]
  rebaseplan cleanup
  rebaseplan propagate-notes [options] <notes-ref>...
  rebaseplan sync-local [options]
  rebaseplan sync-remote [options]


Commands:

  show            Display the relationships between the selected branches. Last
                  locations of the branches their reflog @{1} is tagged with a
                  tag name `rebase/last/$branch_name` so they show up in git
                  log.

  cleanup         Remove the tags added by the tool.

  propagate-notes Search reflogs of all matching branches and copy notes from
                  the specified ref to the current branch commit.

  sync-local      Move local branches that were rebased elsewhere.


Options:
  -h --help                Show help.
  --version                Print version.
  -v --verbose             Show also individual commits.
  --pattern=<pat>          Pattern for matching branches [default: */CORE-130*].
  -a --all                 List all branches
  -n --max-count=<number>  Number of rows to list (passed to git log)
  --view                   Show with gitk.
  --main=<branch>          Name of final destination branch [default: develop].
  --upstream=<remote>      Name of remote [default: origin].
  --show-cmdline           Print out commandline to run instead of invoking it.
  --reflog-depth=N         How many reflog entries to tag so they show up in the
                           graph [default: 10]
  -f --force               Overwrite current notes with old ones
"""

import docopt
import functools

from .rebaseplan import text_view, gitk_view
from .rebaseplan import all_branches, no_extra_flags, compose_flags, additional_flags
from .rebaseplan import rebaseplan, remove_old_tags, propagate_notes, sync_local, sync_remote
from .rebaseplan import run_command, display_command
from .rebaseplan import __version__


def dense_log(*flags):
    return flags + ("--simplify-by-decoration", )


def no_remote_branches(upstream):
    def filter_upstream(*flags):
        return flags + (f"--decorate-refs-exclude=remotes/{upstream}/*", )
    return filter_upstream

def passtrough(args, *flags):
    def read_flags():
        for flag in flags:
            value = args[flag]
            if value is None or isinstance(value, bool):
                if value:
                    yield flag
            else:
                for v in value if isinstance(value, (tuple, list)) else [value]:
                    yield flag
                    yield v
    x = tuple(read_flags())
    return additional_flags(*x)

def is_command(k):
    return k[0] not in "<-"


def main():
    args = docopt.docopt(__doc__, version=__version__)
    # print(args)
    command = [k for k, v in args.items() if k[0] not in "<-" and v]
    if not command:
        args["show"] = True
    run = run_command if not args["--show-cmdline"] else display_command
    if args["show"]:
        rebaseplan(
            pattern=args["--pattern"],
            branch_flags=compose_flags(
                all_branches if args["--all"] else no_extra_flags,
                additional_flags("--no-merged", args["--main"])
                ),
            view=gitk_view if args["--view"] else text_view,
            optional_log_flags=compose_flags(
                (no_extra_flags if args["--verbose"] else dense_log),
                (no_extra_flags if args["--all"]
                 else no_remote_branches(args["--upstream"])),
                additional_flags(*args["<log-options>"]),
                passtrough(args, "--max-count"),
            ),
            main=args["--main"],
            upstream=args["--upstream"],
            run=run,
            reflog_depth=int(args["--reflog-depth"]),
        )
    elif args["cleanup"]:
        remove_old_tags(
            run=run,
        )
    elif args["propagate-notes"]:
        propagate_notes(
            notes_refs=args["<notes-ref>"],
            pattern=args["--pattern"],
            branch_flags=no_extra_flags,
            verbose=args["--verbose"]
        )
    elif args["sync-local"]:
        sync_local(
            pattern=args["--pattern"] + [args["--main"]],
            main=args["--main"],
            upstream=args["--upstream"],
            verbose=args["--verbose"],
            dry_run=not args["--force"]
        )
    elif args["sync-remote"]:
        sync_remote(
            pattern=args["--pattern"] + [args["--main"]],
            main=args["--main"],
            upstream=args["--upstream"],
            verbose=args["--verbose"],
            dry_run=not args["--force"]
        )
    else:
        raise Exception(f"Unhandled command '{command}'")
