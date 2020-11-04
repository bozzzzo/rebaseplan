import collections
import contextlib
import functools
import itertools
import shlex
import subprocess
import sys

import importlib_metadata

__version__ = importlib_metadata.version('rebaseplan')

def subprocess_run(args, **kwargs):
    if kwargs.pop("dry_run", False):
        print("#  ", format_command(args))
        return
    try:
        return subprocess.run(args, **kwargs)
    except subprocess.CalledProcessError as cpe:
        print("ERROR: ", format_command(cpe.cmd), file=sys.stderr, flush=True)
        if cpe.stdout is not None:
            print("", *cpe.stdout.splitlines(), sep="    ", flush=True)
        if cpe.stderr is not None:
            print("", *cpe.stderr.splitlines(), sep="    ", file=sys.stderr, flush=True)
        raise


def all_branches(*flags):
    return flags + ("--all", )


def remote_branches(*flags):
    return flags + ("--remote", )


def no_extra_flags(*flags):
    return flags


def compose_flags(*funcs):
    def composer(*flags):
        return functools.reduce(lambda x, func: func(*x), funcs, flags)
    return composer


def additional_flags(*additional):
    def additional_flags(*flags):
        return flags + additional
    return additional_flags


def list_branches(*, pattern, branch_flags):
    if isinstance(pattern, str):
        pattern = [pattern]
    cmd = subprocess_run(
        ["git", "branch"] + list(branch_flags()) + ["--list"] + pattern,
        text=True, capture_output=True, check=True)
    return [b[2:] for b in cmd.stdout.splitlines()]


def head_reflog(branch, n):
    cmd = subprocess_run(
        ["git", "reflog", "show", branch],
        text=True, capture_output=True, check=True)
    yield branch
    for ref in cmd.stdout.splitlines()[1:n]:
        _, ref, _ = ref.split(maxsplit=2)
        yield ref.rstrip(':')


def branches_with_reflogs(*, pattern, n, branch_flags):
    for branch in list_branches(pattern=pattern, branch_flags=branch_flags):
        yield from head_reflog(branch, n)


def remove_old_tags(*, run):
    cmd = subprocess_run(
        ["git", "for-each-ref", "refs/tags/rebase/last/",
         "--format=%(refname:short)"],
        text=True, capture_output=True, check=True)
    old_tags = cmd.stdout.splitlines()
    if not old_tags:
        return
    args = ["git", "tag", "-d"] + list(old_tags)
    run(args, text=True, capture_output=True, check=True)


def merge_base(*refs):
    cmd = subprocess_run(["git", "merge-base"] + list(refs),
                         text=True, capture_output=True, check=True)
    return cmd.stdout.strip()


def tag_last_branches(*, pattern, branch_flags, main, reflog_depth):
    remove_old_tags(run=run_command)
    branches = list(list_branches(pattern=pattern, branch_flags=branch_flags))
    tags = []
    bases = set()
    last_bases = set()
    for branch in branches:
        bases.add(merge_base(branch, main))
        log = list(head_reflog(branch, 1+reflog_depth))
        for i, last_sha in enumerate(log[1:], 1):
            tag = f"rebase/last/{branch}/{i}"
            tags.append(tag)
            subprocess_run(["git", "tag", "-f", tag, last_sha], check=True)
            last_bases.add(merge_base(tag, main))

    base_tags = []
    for i, base_sha in enumerate(bases):
        base_tag = f"rebase/last/__base__/{i}"
        base_tags.append(base_tag)
        subprocess_run(["git", "tag", "-f", base_tag, base_sha], check=True)
    for i, base_sha in enumerate(last_bases - bases):
        base_tag = f"rebase/last/__last_base__/{i}"
        base_tags.append(base_tag)
        subprocess_run(["git", "tag", "-f", base_tag, base_sha], check=True)

    return branches, tags, base_tags


def no_optional_flags(*flags):
    return flags


def text_view(optional_flags):
    return ["git", "log"] + list(optional_flags("--oneline", "--decorate",
                                                "--color", "--graph",
                                                "--boundary"))


def gitk_view(optional_flags):
    return ["gitk"] + list(optional_flags("--boundary"))


def run_command(args, **kwargs):
    subprocess_run(args, **kwargs)


def format_command(args):
    return " ".join(map(shlex.quote, args))


def display_command(args, **kwargs):
    print(format_command(args))


def rebaseplan(*, pattern,
               branch_flags=no_optional_flags,
               view=text_view,
               optional_log_flags=no_optional_flags,
               main="develop",
               upstream="origin",
               run=run_command,
               reflog_depth=1):
    branches, tags, base_tags = tag_last_branches(pattern=pattern,
                                                  branch_flags=branch_flags,
                                                  main=main,
                                                  reflog_depth=reflog_depth)
    args = (view(optional_log_flags)
            + [f"^{main}^", f"^{upstream}/{main}^"]
            + branches
            )
    run(args, check=True)


Latest = collections.namedtuple("Latest", "commit_id, reflog_name, current_branch")
class branch_reflog:
    def __init__(self, branch):
        cmd = subprocess_run(
            ["git", "reflog", "show", "--date=iso", "--pretty=format:%H %gd", branch],
            text=True, capture_output=True, check=True)
        self.branch = branch
        self.reflog = tuple(tuple(ref.split(maxsplit=1)) for ref in cmd.stdout.splitlines())

    def latest_of(self, commit_ids):
        """returns a (full_commit_id, reflog_name, current_branch_flag)"""
        for i, (commit_id, reflog_name) in enumerate(self.reflog):
            if commit_id in commit_ids:
                return Latest(commit_id, reflog_name, not i)
        return None


def notes_map(notes_ref):
    """returns a map {full_commit_id: notes_id}"""
    cmd = subprocess_run(
        ["git", "notes", "--ref", notes_ref, "list"],
        text=True, capture_output=True, check=True)
    return dict(note.split()[::-1] for note in cmd.stdout.splitlines())


def add_note(*, notes_ref, commit_id, message=None, note=None, force=False):
    if isinstance(message, str):
        message = [message]
    elif message is None:
        message=[]
    else:
        pass
    if note is None:
        note = []
    else:
        note = [note]

    args = (["git", "notes", "--ref", notes_ref, "add"]
            + ["-f"] * bool(force)
            + [f"-m{m}" for m in message]
            + [f"-C{c}" for c in note]
            + [commit_id])
    subprocess_run(args, check=True)


def propagate_notes(*, notes_refs, pattern, branch_flags, verbose=False, force=False):
    reflogs = list(map(branch_reflog,
                       list_branches(pattern=pattern,
                                     branch_flags=branch_flags)))
    for notes_ref in notes_refs:
        notes = notes_map(notes_ref)
        for reflog in reflogs:
            found = reflog.latest_of(notes)
            if found is None:
                continue
            if found.current_branch and not force:
                if verbose:
                    print(f"Note {notes_ref} for {reflog.branch} already exists, skipping.")
                continue
            if verbose:
                print(f"Note {notes_ref} for {reflog.branch} copied from {found.reflog_name}")
            add_note(notes_ref=notes_ref,
                     force=force,
                     message=f"# {found.reflog_name} {found.commit_id}",
                     note=notes[found.commit_id],
                     commit_id=reflog.branch)


def ref_sha(ref):
    cmd = subprocess_run(["git", "rev-parse", ref],
                         text=True, capture_output=True, check=True)
    return cmd.stdout.strip()

def branch_exists(branch):
    return list_branches(pattern=branch,
                         branch_flags=no_extra_flags)


def get_current_branch():
    cmd = subprocess_run(["git", "branch", "--show-current"],
                         text=True, capture_output=True, check=True)
    return cmd.stdout.strip()


@contextlib.contextmanager
def retain_current_branch(*, dry_run):
    current = get_current_branch()
    yield
    if current != get_current_branch():
        args = (["git", "checkout", current])
        subprocess_run(args, check=True, dry_run=dry_run)


def fetch(*, upstream, dry_run):
    args = (["git", "fetch", upstream, "--prune"])
    subprocess_run(args, check=True, dry_run=dry_run)

def sync_local(*, pattern, main,  upstream, verbose=False, dry_run=False):
    fetch(upstream=upstream, dry_run=dry_run)

    new=[]
    unrelated=[]
    uptodate=[]
    stale=[]
    modified=[]

    for remote_branch in filter(
            lambda branch: branch.startswith(f"{upstream}/"),
            list_branches(pattern=pattern,
                          branch_flags=compose_flags(
                              remote_branches,
                              additional_flags("--no-merged", main)))):
        local_branch = remote_branch[len(upstream)+1:]
        if not branch_exists(local_branch):
            new.append(dict(
                local_branch=local_branch,
                remote_branch=remote_branch))
            continue
        result = branch_reflog(remote_branch).latest_of(ref_sha(local_branch))
        if result is None:
            result = branch_reflog(local_branch).latest_of(ref_sha(remote_branch))
            if result is None:
                unrelated.append(dict(
                    local_branch=local_branch,
                    remote_branch=remote_branch))
            else:
                modified.append(dict(
                    local_branch=local_branch,
                    remote_branch=remote_branch,
                    result=result.reflog_name))
        elif result.current_branch:
            uptodate.append(dict(
                local_branch=local_branch,
                remote_branch=remote_branch))
        else:
            stale.append(dict(
                result=result.reflog_name,
                local_branch=local_branch,
                remote_branch=remote_branch))

    with retain_current_branch(dry_run=dry_run):
        for u in uptodate:
            print("Up to date:", u["local_branch"])

        if unrelated:
            print()
        for u in unrelated:
            print("Unrelated:", u["local_branch"], u["remote_branch"])

        if new:
            print()
        for n in new:
            print("New: ", n["local_branch"], n["remote_branch"])
            args = (["git", "branch", "-q", "--track",  n["local_branch"], n["remote_branch"]])
            subprocess_run(args, check=True, dry_run=dry_run)

        if stale:
            print()
        for s in stale:
            print("Switching ", s["local_branch"], "to", s["remote_branch"])
            args = (["git", "checkout", "-q", s["local_branch"]])
            subprocess_run(args, check=True, dry_run=dry_run)
            args = (["git", "reset", "-q", "--hard", s["remote_branch"]])
            subprocess_run(args, check=True, dry_run=dry_run)

        if modified:
            print()
        for m in modified:
            print("Locally modified ", m["local_branch"], "ahead of", m["remote_branch"], "=", m["result"])
