# git log --pretty="%D (%an) %s" --simplify-by-decoration $(git branch -a --list '*feature/CORE-130*' | sed -e 's|..\(remotes/\)*\(.*\)|\2 \2@{1}|') ^develop^
import subprocess

import importlib_metadata

__version__ = importlib_metadata.version('rebaseplan')


def list_branches(*, pattern, branch_flags):
    if isinstance(pattern, str):
        pattern = [pattern]
    cmd = subprocess.run(
        ["git", "branch"] + list(branch_flags()) + ["--list"] + pattern,
        text=True, capture_output=True, check=True)
    return [b[2:] for b in cmd.stdout.splitlines()]

def head_reflog(branch, n):
    cmd = subprocess.run(
        ["git", "reflog", "show", branch],
        text=True, capture_output=True, check=True)
    yield branch
    for ref in cmd.stdout.splitlines()[1:n]:
        _, ref, _ = ref.split(maxsplit=2)
        yield ref.rstrip(':')

def branches_with_reflogs(*, pattern, n, branch_flags):
    for branch in list_branches(pattern=pattern, branch_flags=branch_flags):
        yield from head_reflog(branch, n)

def remove_old_tags():
    cmd = subprocess.run(
        ["git", "for-each-ref", "refs/tags/rebase/last/", "--format=%(refname:short)"],
        text=True, capture_output=True, check=True)
    old_tags = cmd.stdout.splitlines()
    if not old_tags:
        return
    cmd = subprocess.run(
        ["git", "tag", "-d"] + list(old_tags),
        text=True, capture_output=True, check=True)

def merge_base(*refs):
    cmd = subprocess.run(["git", "merge-base"] + list(refs),
                         text=True, capture_output=True, check=True)
    return cmd.stdout.strip()

def tag_last_branches(*, pattern, branch_flags, main):
    remove_old_tags()
    branches = list(list_branches(pattern=pattern, branch_flags=branch_flags))
    tags = []
    bases = set()
    last_bases = set()
    for branch in branches:
        log = list(head_reflog(branch, 2))
        if len(log) < 2:
            continue
        last_sha = log[1]
        tag = "rebase/last/" + branch
        tags.append(tag)
        subprocess.run(["git", "tag", "-f", tag, last_sha], check=True)
        bases.add(merge_base(branch, main))
        last_bases.add(merge_base(tag, main))

    base_tags = []
    for i, base_sha in enumerate(bases):
        base_tag = f"rebase/last/__base__/{i}"
        base_tags.append(base_tag)
        subprocess.run(["git", "tag", "-f", base_tag, base_sha], check=True)
    for i, base_sha in enumerate(last_bases):
        base_tag = f"rebase/last/__last_base__/{i}"
        base_tags.append(base_tag)
        subprocess.run(["git", "tag", "-f", base_tag, base_sha], check=True)

    return branches, tags, base_tags

def no_optional_flags(*flags):
    return flags

def text_view(optional_flags):
    return ["git", "log"] + list(optional_flags("--oneline", "--decorate",
                                                "--color", "--graph",
                                                "--boundary"))


def gitk_view(optional_flags):
    return ["gitk"] + list(optional_flags("--boundary"))


def rebaseplan(*, pattern,
               branch_flags=no_optional_flags,
               view=text_view,
               optional_log_flags=no_optional_flags,
               main="develop",
               origin="origin"):
    branches, tags, base_tags = tag_last_branches(pattern=pattern, branch_flags=branch_flags, main=main)
    args = (view(optional_log_flags)
            + [f"^{main}^", f"^{origin}/{main}^"]
            + branches
            )
    cmd = subprocess.run(args, check=True)

