"""Does the test actually discriminate, or would it pass either way?

A test that passes whether or not the change is present proves nothing, and it
is the failure shape every "did you run the tests" gate misses, because the
tests really did run and really were green.

The check: build a throwaway git worktree at HEAD (the code WITHOUT this
turn's uncommitted work), run the same test command there, and see what
happens.

    test fails without the change  -> PROVEN      (it discriminates)
    test passes without the change -> INERT       (it proves nothing)
    test cannot run there          -> INCONCLUSIVE

Nothing here touches the working tree. `git worktree add` only ever writes
into a fresh temp directory, and it is removed afterwards.
"""
import os, re, shutil, subprocess, tempfile

PROVEN, INERT, INCONCLUSIVE = "PROVEN", "INERT", "INCONCLUSIVE"

#: dependency directories worth reusing rather than reinstalling. They are
#: linked, not copied, and the run is read-only as far as they are concerned.
DEP_DIRS = ["node_modules", ".venv", "venv", "vendor", "target", ".bundle",
            "Pods", ".gradle", "__pypackages__"]


def _git(args, cwd, timeout=60):
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def repo_root(cwd):
    code, out, _ = _git(["rev-parse", "--show-toplevel"], cwd)
    return out if code == 0 and out else None


def changed_files(root):
    """Uncommitted work: tracked modifications plus untracked files."""
    _, tracked, _ = _git(["diff", "--name-only", "HEAD"], root)
    _, untracked, _ = _git(["ls-files", "--others", "--exclude-standard"], root)
    files = [f for f in (tracked + "\n" + untracked).splitlines() if f.strip()]
    return sorted(set(files))


def run(command, cwd, timeout=600):
    try:
        p = subprocess.run(command, cwd=cwd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "timed out after %ds" % timeout
    except OSError as e:
        return None, str(e)


ENV_BROKEN = [
    r"command not found", r"No such file or directory",
    r"ModuleNotFoundError", r"ImportError", r"Cannot find module",
    r"is not recognized as an internal", r"error: could not find",
    r"no tests ran", r"No tests found", r"collected 0 items",
    r"\bpanic:\b.*cannot find package",
]


def check(command, cwd, timeout=600, link_deps=True):
    """Run `command` against HEAD in a disposable worktree.

    Returns (status, detail) where status is PROVEN / INERT / INCONCLUSIVE.
    """
    root = repo_root(cwd)
    if not root:
        return INCONCLUSIVE, "not inside a git repository, so there is no HEAD to compare against"

    code, head, _ = _git(["rev-parse", "HEAD"], root)
    if code != 0:
        return INCONCLUSIVE, "repository has no commits yet"

    changed = changed_files(root)
    if not changed:
        return INCONCLUSIVE, ("nothing is uncommitted, so HEAD already contains the change -- "
                              "commit first, or run this before committing")

    tmp = tempfile.mkdtemp(prefix="receipts-inert-")
    work = os.path.join(tmp, "head")
    code, _, err = _git(["worktree", "add", "--detach", work, "HEAD"], root, timeout=180)
    if code != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return INCONCLUSIVE, "could not create a worktree at HEAD: %s" % err[:200]

    try:
        if link_deps:
            for d in DEP_DIRS:
                src, dst = os.path.join(root, d), os.path.join(work, d)
                if os.path.isdir(src) and not os.path.exists(dst):
                    try:
                        os.symlink(src, dst)
                    except OSError:
                        pass

        rc, output = run(command, work, timeout=timeout)
        tail = output.strip()[-1200:]

        if rc is None:
            return INCONCLUSIVE, "the command could not complete at HEAD: %s" % tail[:300]

        for pat in ENV_BROKEN:
            if re.search(pat, output, re.I):
                return INCONCLUSIVE, ("the command did not really run at HEAD "
                                      "(%s) -- the environment, not the test, decided this\n\n%s"
                                      % (re.search(pat, output, re.I).group(0)[:60], tail))

        if rc != 0:
            return PROVEN, ("the test fails at HEAD and passes with your change, "
                            "so it is actually testing the change\n\n%s" % tail)

        return INERT, ("the test passes at HEAD too -- it would be green with or "
                       "without your change, so it does not prove the change works\n\n%s" % tail)
    finally:
        _git(["worktree", "remove", "--force", work], root, timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)
