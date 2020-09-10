
"""rebaseplan

Usage:
  rebaseplan [show] [options] [(--pattern=<pat>)...] [(-- <log-options>...)]
  rebaseplan cleanup


Commands:
  show      Display the relationships between the selected branches.
            Last locations of the branches their reflog @{1} is tagged with a
            tag name `rebase/last/$branch_name` so they show up in git log.
  cleanup   Remove the tags added by the tool.


Options:
  -h --help            Show help.
  --version            Print version.
  -v --verbose         Show also individual commits.
  --pattern=<pat>      Pattern for matching branches [default: */CORE-130*].
  -a --all             List all branches
  --view               Show with gitk.
  --main=<branch>      Name of final destination branch [default: develop].
  --upstream=<remote>  Name of remote [default: origin].
  --show-cmdline       Print out commandline to run instead of invoking it.
"""

import docopt
import functools

from .rebaseplan import text_view, gitk_view
from .rebaseplan import rebaseplan, remove_old_tags
from .rebaseplan import run_command, display_command
from .rebaseplan import __version__


def no_extra_flags(*flags):
    return flags


def dense_log(*flags):
    return flags + ("--simplify-by-decoration", )


def no_remote_branches(origin):
    def filter_origin(*flags):
        return flags + (f"--decorate-refs-exclude=remotes/{origin}/*", )
    return filter_origin


def compose_flags(*funcs):
    def composer(*flags):
        return functools.reduce(lambda x, func: func(*x), funcs, flags)
    return composer


def all_branches(*flags):
    return flags + ("--all", )


def additional_flags(*additional):
    def additional_flags(*flags):
        return flags + additional
    return additional_flags


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
            branch_flags=all_branches if args["--all"] else no_extra_flags,
            view=gitk_view if args["--view"] else text_view,
            optional_log_flags=compose_flags(
                (no_extra_flags if args["--verbose"] else dense_log),
                (no_extra_flags if args["--all"]
                 else no_remote_branches(args["--upstream"])),
                additional_flags(*args["<log-options>"]),
            ),
            main=args["--main"],
            origin=args["--upstream"],
            run=run,
        )
    elif args["cleanup"]:
        remove_old_tags(
            run=run)
