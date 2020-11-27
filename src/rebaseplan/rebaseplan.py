import collections
import contextlib
import functools
import itertools
import shlex
import subprocess
import sys
import typing
import enum

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
        ["git", "reflog", "show", branch, "--"],
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


ReflogEntry = collections.namedtuple("ReflogEntry", "commit_id, reflog_name, current_branch, i")
class branch_reflog:
    def __init__(self, branch):
        cmd = subprocess_run(
            ["git", "reflog", "show", "--date=iso", "--pretty=format:%H %gd", branch],
            text=True, capture_output=True, check=True)
        self.branch = branch
        self.reflog = tuple(ReflogEntry(*ref.split(maxsplit=1), not i, i) for i, ref in enumerate(cmd.stdout.splitlines()))

    def latest_of(self, commit_ids):
        """returns a (full_commit_id, reflog_name, current_branch_flag)"""
        for entry in self.reflog:
            if entry.commit_id in commit_ids:
                return entry
        return None

    @property
    def commit_ids(self):
        return tuple(entry.commit_id for entry in self.reflog)


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


class BranchSyncStatus(enum.IntEnum):
    NEW_LOCAL = enum.auto()
    UNRELATED = enum.auto()
    UPTODATE = enum.auto()
    REMOTE_MODIFIED = enum.auto()
    LOCAL_MODIFIED = enum.auto()
    CONFLICTED = enum.auto()


class BranchSyncState(typing.NamedTuple):
    status: BranchSyncStatus
    local_branch: str
    remote_branch: str
    local_reflog: ReflogEntry = None
    remote_reflog: ReflogEntry = None


def branch_sync_state(*, pattern, main,  upstream):
    for remote_branch in filter(
            lambda branch: branch.startswith(f"{upstream}/"),
            list_branches(pattern=pattern,
                          branch_flags=compose_flags(
                              remote_branches,
                              additional_flags("--no-merged", main)))):
        local_branch = remote_branch[len(upstream)+1:]
        state = BranchSyncState(None,
                local_branch=local_branch,
                remote_branch=remote_branch)

        if not branch_exists(local_branch):
            yield state._replace(status=BranchSyncStatus.NEW_LOCAL)
            continue

        remote_reflog = branch_reflog(remote_branch)
        local_reflog = branch_reflog(local_branch)
        state = state._replace(
            remote_reflog=remote_reflog.latest_of(ref_sha(local_branch)),
            local_reflog=local_reflog.latest_of(ref_sha(remote_branch)))

        if state.remote_reflog is not None and state.remote_reflog.current_branch:
            yield state._replace(status=BranchSyncStatus.UPTODATE)
        elif state.remote_reflog is not None:
            yield state._replace(status=BranchSyncStatus.REMOTE_MODIFIED)
        elif state.local_reflog is not None:
            yield state._replace(status=BranchSyncStatus.LOCAL_MODIFIED)
        else:
            state = state._replace(
                remote_reflog=remote_reflog.latest_of(local_reflog.commit_ids),
                local_reflog=local_reflog.latest_of(remote_reflog.commit_ids))

            if state.remote_reflog is None and state.local_reflog is None:
                yield state._replace(status=BranchSyncStatus.UNRELATED)
            else:
                yield state._replace(status=BranchSyncStatus.CONFLICTED)


def sync_local(*, pattern, main,  upstream, verbose=False, dry_run=False):
    with retain_current_branch(dry_run=dry_run):
        fetch(upstream=upstream, dry_run=dry_run)

        last_status = None
        for state in sorted(branch_sync_state(pattern=pattern, main=main, upstream=upstream)):
            if last_status != state.status:
                print()
                last_status = state.status

            if state.status == BranchSyncStatus.NEW_LOCAL:
                print(state.status, state.local_branch, state.remote_branch)
                args = ["git", "branch", "-q", "--track", state.local_branch, state.remote_branch]
                subprocess_run(args, check=True, dry_run=dry_run)

            elif state.status == BranchSyncStatus.REMOTE_MODIFIED:
                print("Switching", state.status, state.local_branch, "to", state.remote_branch)
                args = ["git", "checkout", "-q", state.local_branch]
                subprocess_run(args, check=True, dry_run=dry_run)
                args = ["git", "reset", "-q", "--hard", state.remote_branch]
                subprocess_run(args, check=True, dry_run=dry_run)

            elif state.status == BranchSyncStatus.LOCAL_MODIFIED:
                print(state.status, state.local_branch, "ahead of", state.remote_branch, "=", state.local_reflog)

            else:
                print(state.status, state.local_branch, state.remote_branch)

def sync_remote(*, pattern, main,  upstream, verbose=False, dry_run=False):
        fetch(upstream=upstream, dry_run=dry_run)

        force_pushes = []
        last_status = None
        for state in sorted(branch_sync_state(pattern=pattern, main=main, upstream=upstream)):
            if last_status != state.status:
                if verbose:
                    print()
                last_status = state.status

            if state.status == BranchSyncStatus.LOCAL_MODIFIED:
                if verbose:
                    print("Will push", state.local_branch, "ahead of", state.remote_branch, "=", state.local_reflog)
                force_pushes.append(state.local_branch)
            else:
                if verbose or state.status in (BranchSyncStatus.CONFLICTED, BranchSyncStatus.REMOTE_MODIFIED):
                    print(state.status, state.local_branch, state.remote_branch)

        if force_pushes:
            args = (["git", "push", upstream, "-f"] + force_pushes)
            subprocess_run(args, check=True, dry_run=dry_run)
        else:
            print("Nothing to do")
