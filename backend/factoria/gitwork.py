"""Every `git` command Factor-ia runs, and nothing else may run one.

This module is the mechanical half of the blast-radius requirement
(`DESIGN.md`, Non-functional requirements): *"Agents write only inside a git
worktree on a dedicated branch. No force-push, no writes to the default branch,
no deletes outside the worktree."* Convention cannot enforce that. A single
choke point can, so:

- **`_git` refuses any subcommand not on `ALLOWED_SUBCOMMANDS`.** `fetch`,
  `reset`, `clean`, `checkout`, `switch`, `rebase`, `merge`, `update-ref` and
  friends are simply absent, so the capability does not exist in the process at
  all — a future edit that tries to reset a branch raises `GitDenied` before a
  child is spawned, and a test asserts it. **There is no `merge` here and there
  will not be**: a local merge means writing to a branch the operator has
  checked out, which is the one thing this whole design promises not to do. The
  `commit-merge` node merges *on the remote*, with `push`, below.
- **`branch` may be read, never deleted.** `-d`/`-D`/`--delete` is refused, on
  any subcommand. A run's branch is the artifact the operator reviews; we
  create branches and we never remove one, not even our own.
- **`--force` is accepted for exactly one thing:** `worktree remove --force`,
  which discards a worktree *we* created under `_runs/`. It is refused
  everywhere else, which is what makes "no force-push" structural rather than
  a promise.
- **`push` exists now, and is the narrowest subcommand in the file.** The
  `commit-push` and `commit-merge` output nodes need it (`models.OutputKind`),
  so the capability can no longer be "absent from the process". What replaces
  absence is a shape check: a push is *exactly* `git push <remote-name>
  <src>:<dst>`, with a flag allowlist of three read-only flags, a refspec that
  must name both sides, and a leading `+` refused outright. Everything a push
  can be abused for — `--force`, `--force-with-lease`, `--delete`, `--mirror`,
  `--all`, `--tags`, `+ref:ref`, a bare `<remote>` that pushes whatever
  `push.default` says — is a `GitDenied` before a child exists.

  Two consequences worth stating rather than discovering:

  - **The remote is a name, never a URL.** `git push git@elsewhere:x.git …` is
    a working exfiltration of the operator's repository using the operator's
    own deploy key, and a factory file is a document that can arrive from
    anywhere. A name has to already be configured in the repo the operator
    put in the workspace, which is a decision they made outside this process.
  - **No force means no history rewriting and no merge that isn't a
    fast-forward.** A `commit-merge` into a base branch that has moved is
    refused *by the remote*, and the node fails with git's own words. That is
    the whole of the merge safety story: we never resolve a conflict, because
    we never have a conflict to resolve.

Two more scars, paid for rather than discovered:

- **Hooks are disabled on every invocation.** A codebase's `.git/hooks` is the
  operator's own code, and `git commit`/`git worktree add` would run it inside
  the container, as the container, with the container's credentials. `-c
  core.hooksPath=<nonexistent>` plus `--no-verify` is the whole mitigation.
- **The commit identity is validated, not discovered at commit time.** Compose
  passes `GIT_AUTHOR_*` / `GIT_COMMITTER_*` through as bare names, so on a host
  that sets none they are simply absent and the container — which has no
  `.gitconfig` at all — would fail `git commit` with "empty ident". `identity()`
  resolves the four values up front, treats a **set-but-blank** variable as an
  error rather than a default, and falls back to a stated Factor-ia identity
  when they are absent.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Every git subcommand this process is allowed to run. Read-only ones plus the
#: four writes a run needs: make a worktree, stage, commit, and — for the two
#: pushing output kinds — put a ref on a remote.
ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "rev-parse",
        "symbolic-ref",
        "status",
        "log",
        "diff",
        "show",
        "branch",  # listing/verifying only — see `_check`
        "worktree",
        "add",
        "commit",
        "push",  # one shape only — see `_check_push`
        "remote",  # `get-url` only — see `_check`
        "ls-remote",  # read-only, and the pre-flight a pushing node needs
        "var",
        "init",  # tests build repos with it; harmless and never reaches a codebase
        "config",
    }
)

#: Arguments that are refused everywhere except the one place named below.
_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease"})
_DELETE_FLAGS = frozenset({"-d", "-D", "--delete", "--remove"})

#: The only flags a `push` may carry. All three are about *reporting* — none of
#: them changes what is written. Anything else is refused, which is what makes
#: the list of dangerous push flags something we never have to keep up to date.
_PUSH_FLAGS = frozenset({"--porcelain", "--atomic", "--verbose", "-v"})

#: A remote *name*, never a URL, and never a path. See the module docstring.
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: One side of a refspec. Deliberately narrower than git's own rules: no `+`,
#: no `^`, no `~`, no `@{…}`, no glob, no whitespace.
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

#: Hooks live in the *operator's* repo. Running them would execute host-authored
#: code inside the container on every commit.
_NO_HOOKS = ["-c", "core.hooksPath=/nonexistent/factoria-no-hooks"]

DEFAULT_TIMEOUT_S = 120.0

#: Used when the environment states no identity at all. A commit has to say who
#: made it, and "Factor-ia" is more honest than borrowing a human's name.
FALLBACK_NAME = "Factor-ia"
FALLBACK_EMAIL = "factor-ia@localhost"

#: How many changed paths a `TreeState` carries. The canvas shows a handful and
#: a repo mid-`npm install` has 30,000.
MAX_LISTED_CHANGES = 12


class GitError(RuntimeError):
    """A git command failed. Carries the command and what git said about it."""

    def __init__(self, argv: list[str], code: int, stderr: str) -> None:
        self.argv = argv
        self.code = code
        self.stderr = stderr.strip()
        detail = self.stderr.splitlines()[-1] if self.stderr else f"exit {code}"
        super().__init__(f"git {' '.join(argv[:3])}: {detail}")


class GitDenied(GitError):
    """The command was refused here, before a child existed. Always a bug."""

    def __init__(self, argv: list[str], why: str) -> None:
        RuntimeError.__init__(self, f"refused to run `git {' '.join(argv)}`: {why}")
        self.argv = argv
        self.code = -1
        self.stderr = why


class IdentityProblem(ValueError):
    """The commit identity in the environment cannot produce a commit."""


def _check(args: list[str]) -> None:
    """The gate. Raises `GitDenied` rather than spawning anything."""
    if not args:
        raise GitDenied(args, "no subcommand")
    subcommand = args[0]
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise GitDenied(args, f"`{subcommand}` is not an allowed subcommand")
    rest = args[1:]
    if any(flag in _DELETE_FLAGS for flag in rest) and subcommand in ("branch", "push"):
        # On `branch` this is "we never delete a ref". On `push` it is the same
        # sentence about someone else's repository, which is the more expensive
        # one to get wrong.
        raise GitDenied(args, "refs are never deleted, not even ours")
    force = [flag for flag in rest if flag in _FORCE_FLAGS]
    if force:
        # The one exception: discarding a worktree *we* created under `_runs/`.
        if not (subcommand == "worktree" and rest[:1] == ["remove"]):
            raise GitDenied(args, f"{force[0]} is only allowed on `worktree remove`")
    if subcommand == "push":
        _check_push(args, rest)
    if subcommand == "remote" and rest[:1] != ["get-url"]:
        # `remote add`/`set-url` would let a factory file point a push at a
        # host of its choosing; `remote show` talks to the network to answer a
        # question we never ask. Reading a configured URL is the whole need.
        raise GitDenied(args, "`remote` may only be used as `remote get-url`")


def _check_push(args: list[str], rest: list[str]) -> None:
    """A push is `git push [safe flags] <remote-name> <src>:<dst>`. Only that.

    Whitelisting the *shape* rather than blacklisting flags is the point: a
    future git that grows a new way to clobber a remote ref does not quietly
    become reachable from here, because it will not be one of these three flags
    and it will not be a `src:dst` refspec.
    """
    flags = [item for item in rest if item.startswith("-")]
    positional = [item for item in rest if not item.startswith("-")]
    unknown = [flag for flag in flags if flag not in _PUSH_FLAGS]
    if unknown:
        raise GitDenied(args, f"{unknown[0]} is not an allowed push flag")
    if len(positional) != 2:
        # A bare `git push` — or `git push origin` — pushes whatever
        # `push.default` and the branch's upstream say, which is a decision
        # made by a config file we did not write.
        raise GitDenied(args, "a push must name exactly one remote and one refspec")
    remote, refspec = positional
    if not _REMOTE_NAME.match(remote):
        raise GitDenied(
            args,
            f"{remote!r} is not a remote name — a push may only target a remote "
            "already configured in the repository, never a URL or a path",
        )
    if refspec.startswith("+"):
        raise GitDenied(args, "a leading `+` is a force push")
    source, separator, destination = refspec.partition(":")
    if not separator:
        raise GitDenied(args, "a push refspec must name both sides, as `src:dst`")
    if not _REF.match(source) or not _REF.match(destination):
        raise GitDenied(args, f"{refspec!r} is not a plain `src:dst` refspec")


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    # Nothing this process runs may sit waiting for a credential that no human
    # is present to type. A prompt would hang a run until the watchdog fires.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new")
    if extra:
        env.update(extra)
    return env


def _git(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run one allowed git command. Synchronous — callers use `to_thread`."""
    _check(args)
    argv = ["git", *_NO_HOOKS, *args]
    try:
        done = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=_env(env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(args, -1, str(error)) from error
    if check and done.returncode != 0:
        raise GitError(args, done.returncode, done.stderr)
    return done


# ---- reading a working tree -------------------------------------------------


@dataclass(frozen=True)
class TreeState:
    """What `git status` says, in the shape the API and the canvas need.

    `dirty` is the clean-repo precondition's whole answer; `changes` is the
    legible reason that goes with the refusal.
    """

    is_repo: bool = False
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    dirty: bool = False
    change_count: int = 0
    changes: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def unborn(self) -> bool:
        """A repo with no commits yet. Nothing to branch a worktree from."""
        return self.is_repo and self.head is None


def tree_state(path: Path | str) -> TreeState:
    """Read a codebase's git state. Never raises — an unreadable repo is a
    `TreeState` that says so, because every caller renders it rather than
    handling it."""
    try:
        inside = _git(["rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
    except GitError as error:
        return TreeState(detail=error.stderr or str(error))
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return TreeState(detail=(inside.stderr.strip() or "not a git repository"))

    branch: str | None = None
    detached = True
    named = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path, check=False)
    if named.returncode == 0:
        branch = named.stdout.strip() or None
        detached = False

    head: str | None = None
    resolved = _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=path, check=False)
    if resolved.returncode == 0:
        head = resolved.stdout.strip() or None

    status = _git(
        ["status", "--porcelain=v1", "--untracked-files=all"], cwd=path, check=False
    )
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    return TreeState(
        is_repo=True,
        branch=branch,
        detached=detached,
        head=head,
        dirty=bool(lines),
        change_count=len(lines),
        changes=tuple(line.strip() for line in lines[:MAX_LISTED_CHANGES]),
        detail="" if lines else "clean",
    )


def branch_exists(repo: Path | str, branch: str) -> bool:
    done = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, check=False
    )
    return done.returncode == 0


def current_branch(worktree: Path | str) -> str | None:
    done = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=worktree, check=False)
    return done.stdout.strip() or None if done.returncode == 0 else None


def head_commit(worktree: Path | str) -> str | None:
    done = _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=worktree, check=False)
    return done.stdout.strip() or None if done.returncode == 0 else None


def log_line(repo: Path | str, ref: str) -> str:
    done = _git(["log", "-1", "--format=%h %s", ref], cwd=repo, check=False)
    return done.stdout.strip()


def diff_stat(worktree: Path | str, base: str, head: str = "HEAD") -> str:
    done = _git(["diff", "--stat", f"{base}..{head}"], cwd=worktree, check=False)
    return done.stdout.strip()


def changed_files(worktree: Path | str, base: str, head: str = "HEAD") -> list[str]:
    done = _git(["diff", "--name-only", f"{base}..{head}"], cwd=worktree, check=False)
    return [line for line in done.stdout.splitlines() if line.strip()]


# ---- worktrees --------------------------------------------------------------


def add_worktree(repo: Path | str, path: Path | str, branch: str, base: str) -> None:
    """`git worktree add -b <branch> <path> <base>` — a new branch and a new
    checkout in one call, so the branch can never exist without the worktree
    that is allowed to write to it."""
    _git(["worktree", "add", "-b", branch, str(path), base], cwd=repo)


def remove_worktree(repo: Path | str, path: Path | str) -> None:
    """Discard a worktree and the admin entry and lock that go with it.

    `--force` because the worktree is dirty by definition — an agent just wrote
    in it — and `prune` afterwards because a `remove` that could not delete the
    directory still leaves `.git/worktrees/<name>` behind, which is the
    "orphaned worktree or lock" a cancel must not produce.
    """
    _git(["worktree", "remove", "--force", str(path)], cwd=repo, check=False)
    prune_worktrees(repo)


def prune_worktrees(repo: Path | str) -> None:
    _git(["worktree", "prune"], cwd=repo, check=False)


def list_worktrees(repo: Path | str) -> list[str]:
    done = _git(["worktree", "list", "--porcelain"], cwd=repo, check=False)
    return [
        line.split(" ", 1)[1].strip()
        for line in done.stdout.splitlines()
        if line.startswith("worktree ")
    ]


# ---- remotes and pushes -----------------------------------------------------
#
# The only part of this module that talks to another machine. Everything here
# is shaped by `_check_push` above: a push names a configured remote and a
# `src:dst` refspec, and that is the entire vocabulary.


#: The remote a pushing output node uses unless its config names another.
DEFAULT_REMOTE = "origin"

#: Longer than `DEFAULT_TIMEOUT_S`, because this one crosses a network and a
#: first push of a large branch is legitimately slow. Still bounded: a run must
#: not be able to hold the single run slot on a hung TCP connection.
PUSH_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class PushResult:
    remote: str
    source: str
    destination: str
    #: git's own summary line. Kept verbatim — when a push is refused, git says
    #: why far better than a paraphrase would.
    detail: str
    #: True when the remote already had this exact commit. Not a failure: a
    #: `commit-merge` whose base branch is already at the run's commit did what
    #: it was asked to, and saying so beats inventing an error.
    already_current: bool = False


def remote_url(repo: Path | str, remote: str = DEFAULT_REMOTE) -> str | None:
    """The configured URL for `remote`, or `None` when there is no such remote.

    Read rather than assumed: a pushing output node is refused at run-check time
    when this is `None`, which is a sentence the operator can act on before an
    agent has spent anything.
    """
    done = _git(["remote", "get-url", remote], cwd=repo, check=False)
    return done.stdout.strip() or None if done.returncode == 0 else None


def remote_reachable(repo: Path | str, remote: str = DEFAULT_REMOTE) -> tuple[bool, str]:
    """Can we actually talk to `remote`? Returns `(ok, what git said)`.

    A separate question from `remote_url`, and worth asking separately: a
    configured remote the container has no key for is the single most likely way
    a `commit-push` node fails, and it fails *after* the agent has been paid for.
    """
    done = _git(
        ["ls-remote", "--heads", remote],
        cwd=repo,
        check=False,
        timeout=PUSH_TIMEOUT_S,
    )
    if done.returncode == 0:
        return True, ""
    return False, (done.stderr.strip().splitlines() or ["unreachable"])[-1]


def push_ref(
    worktree: Path | str,
    source: str,
    destination: str,
    *,
    remote: str = DEFAULT_REMOTE,
) -> PushResult:
    """`git push <remote> <source>:<destination>`, and nothing else.

    No force, ever — so a `destination` that has moved since the run branched is
    **refused by the remote** and raises `GitError` carrying git's own
    "non-fast-forward" wording. That refusal is the feature: a `commit-merge`
    node either fast-forwards the base branch or fails loudly, and there is no
    third outcome in which this process decides how to reconcile two histories.
    """
    done = _git(
        ["push", remote, f"{source}:{destination}"],
        cwd=worktree,
        check=False,
        timeout=PUSH_TIMEOUT_S,
    )
    # git puts its progress and its summary on stderr even when it succeeds.
    said = (done.stderr.strip() or done.stdout.strip()).strip()
    if done.returncode != 0:
        raise GitError(["push", remote, f"{source}:{destination}"], done.returncode, said)
    lines = [line for line in said.splitlines() if line.strip()]
    detail = lines[-1] if lines else f"pushed {source} to {remote}/{destination}"
    return PushResult(
        remote=remote,
        source=source,
        destination=destination,
        detail=detail,
        already_current="up-to-date" in said.lower() or "up to date" in said.lower(),
    )


# ---- identity and commits ---------------------------------------------------


@dataclass(frozen=True)
class Identity:
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    #: True when every value came from the fallback rather than the environment.
    from_env: bool = False

    def env(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.committer_name,
            "GIT_COMMITTER_EMAIL": self.committer_email,
        }

    def describe(self) -> str:
        if self.author_name == self.committer_name and self.author_email == self.committer_email:
            return f"{self.author_name} <{self.author_email}>"
        return (
            f"{self.author_name} <{self.author_email}> "
            f"(committer {self.committer_name} <{self.committer_email}>)"
        )


def identity(environ: dict[str, str] | None = None) -> Identity:
    """The four `GIT_*` values a commit needs, validated up front.

    Raises `IdentityProblem` when a variable is **set but blank** — which is
    exactly what `${VAR:-}` in a compose file produces on a host that sets none,
    and exactly what beats the configured identity and fails `git commit` with
    "empty ident name". Absent is fine and falls back; blank is a
    misconfiguration and says so, at run-check time rather than at the end of a
    run that has already spent tokens.
    """
    env = os.environ if environ is None else environ
    resolved: dict[str, str] = {}
    seen_any = False
    for key, fallback in (
        ("GIT_AUTHOR_NAME", FALLBACK_NAME),
        ("GIT_AUTHOR_EMAIL", FALLBACK_EMAIL),
        ("GIT_COMMITTER_NAME", None),
        ("GIT_COMMITTER_EMAIL", None),
    ):
        if key in env:
            value = env[key]
            if not value.strip():
                raise IdentityProblem(
                    f"{key} is set but empty — git reads it ahead of every config "
                    "file, so an empty value fails `git commit` outright. Unset it "
                    "or give it a value (compose must pass it as a bare name, "
                    "never `${" + key + ":-}`)."
                )
            resolved[key] = value.strip()
            seen_any = True
        elif fallback is not None:
            resolved[key] = fallback
    # The committer defaults to the author, which is what git itself does.
    resolved.setdefault("GIT_COMMITTER_NAME", resolved["GIT_AUTHOR_NAME"])
    resolved.setdefault("GIT_COMMITTER_EMAIL", resolved["GIT_AUTHOR_EMAIL"])
    return Identity(
        author_name=resolved["GIT_AUTHOR_NAME"],
        author_email=resolved["GIT_AUTHOR_EMAIL"],
        committer_name=resolved["GIT_COMMITTER_NAME"],
        committer_email=resolved["GIT_COMMITTER_EMAIL"],
        from_env=seen_any,
    )


@dataclass(frozen=True)
class CommitResult:
    commit: str
    subject: str
    files: tuple[str, ...]
    stat: str
    created: bool


def commit_all(
    worktree: Path | str,
    message: str,
    *,
    expect_branch: str,
    base: str,
    ident: Identity | None = None,
) -> CommitResult | None:
    """Stage everything in the worktree and commit it on its own branch.

    Returns `None` when the branch is still exactly at `base` and the worktree
    is clean — that is a run that produced nothing, and the caller says so
    rather than reporting a commit that does not exist.

    **The branch guard is the point.** Before writing anything it asserts that
    the worktree's HEAD is the run's own branch. A commit can therefore never
    land on the operator's checked-out branch, even if the path handed in were
    wrong: the wrong path is a different branch, and a different branch is a
    refusal.
    """
    on = current_branch(worktree)
    if on != expect_branch:
        raise GitError(
            ["commit"],
            -1,
            f"refusing to commit: {worktree} is on {on!r}, not the run's branch "
            f"{expect_branch!r}",
        )
    ident = ident or identity()

    _git(["add", "--all", "."], cwd=worktree)
    staged = _git(["status", "--porcelain=v1"], cwd=worktree, check=False)
    has_changes = bool(staged.stdout.strip())
    if has_changes:
        _git(
            # `--no-verify`: hooks are the operator's code and this is the
            # container. See the module docstring.
            # `--message=` joined, not a separate argument: a message that
            # happened to spell `--force` must not be read as a flag by our own
            # gate above, nor by git.
            ["commit", "--no-verify", f"--message={message}"],
            cwd=worktree,
            env=ident.env(),
        )

    head = head_commit(worktree)
    if head is None or head == base:
        return None
    return CommitResult(
        commit=head,
        subject=log_line(worktree, "HEAD"),
        files=tuple(changed_files(worktree, base)),
        stat=diff_stat(worktree, base),
        # False when the agent had already committed its own work and we found
        # nothing left to stage — the branch still carries a change to review.
        created=has_changes,
    )
