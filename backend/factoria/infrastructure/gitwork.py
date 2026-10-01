"""Every `git` command Factor-ia runs, and nothing else may run one.

This module is the mechanical half of the blast-radius requirement
(`DESIGN.md`, Non-functional requirements): *"Agents write only inside a git
worktree on a dedicated branch. No force-push, no writes to the default branch,
no deletes outside the worktree."* Convention cannot enforce that. A single
choke point can, so:

- **`_git` refuses any subcommand not on `ALLOWED_SUBCOMMANDS`.** `reset`,
  `clean`, `checkout`, `switch`, `rebase`, `merge`, `update-ref` and friends
  are simply absent — `fetch` is present in one shape only (`_check_fetch`) —
  so the capability does not exist in the process at all. A future edit that
  tries to reset a branch raises `GitDenied` before a child is spawned, and a
  test asserts it. **There is no `merge` here and there
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

- **No command a config file names is ever run.** A repository's config and
  attributes are not ours — a codebase may arrive as a tarball, and an agent
  can write to one — and git will execute a surprising number of commands
  named in them, inside the container, as the container, with its credentials.
  The image ships no git config of its own, so every such command is foreign.
  `_git` closes each vector we know of, on every invocation:

  - *Hooks* — `core.hooksPath` pinned to a path that does not exist, plus
    `--no-verify` on `commit`.
  - *`core.fsmonitor`* (run by `status`, `add`, `commit`), *`core.askPass`*,
    *`core.alternateRefsCommand`*, *`credential.helper`* and the signing
    programs (`gpg.*program`, with `commit.gpgSign`, `tag.gpgSign`,
    `push.gpgSign` and `log.showSignature` off) — pinned at command scope
    (`_PINNED`), which beats the repo's value.
  - *Filters* (`filter.<name>.clean`/`.smudge`/`.process`, run by `status`,
    `add`, `commit`, `worktree add`) and *URL-scoped credential helpers*
    (`credential.<url>.helper`) — the repo chooses their names, so
    `_foreign_commands` reads the config before each command and blanks every
    one it finds.
  - *`diff.external`, `diff.<driver>.command`, `diff.<driver>.textconv`* —
    `--no-ext-diff --no-textconv` on `diff`, `log` and `show`. Blanking them
    instead makes git fail with "cannot run".
  - *`core.sshCommand`* and *`core.gitProxy`* — shadowed by `GIT_SSH_COMMAND`
    and an empty `GIT_PROXY_COMMAND` in the environment.
  - *`ext::<command>` URLs and unlisted remote helpers* — `GIT_ALLOW_PROTOCOL`
    permits `file`, `git`, `ssh`, `http` and `https` only, whatever
    `protocol.*.allow` says.
  - *`remote.<name>.uploadpack`/`.receivepack`* — `-c` loses to these (git
    takes the first value), so `fetch`, `ls-remote` and `push` carry an
    explicit `--upload-pack`/`--receive-pack`. For a remote that is a path on
    this machine, git runs that program in the *other* repository, under its
    config and hooks, and strips our pins on the way; so the program named is
    `git` with the pins that matter written into the command, and a push
    never checks files out there.
  - *Submodules* — each is a repository whose config the pins cannot see, so
    git is kept out of them: `status` reports a submodule whose commit moved,
    but not uncommitted edits inside one.

  Two limits, stated rather than discovered. A key written into the config
  between `_foreign_commands` reading it and the command starting is not
  blanked; the window is one process spawn wide. And a remote whose URLs mix a
  local path with a network address is refused, because one pack program
  cannot be right for both.
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
        "fetch",  # one shape only — see `_check_fetch`
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

#: The only flags a `fetch` may carry, and all three are about *reporting*.
#: `--prune` is deliberately absent: it deletes remote-tracking refs, and "refs
#: are never deleted" is a rule this module keeps even for refs it could
#: reconstruct in one round trip.
_FETCH_FLAGS = frozenset({"--quiet", "-q", "--no-tags", "--no-recurse-submodules"})

#: A remote *name*, never a URL, and never a path. See the module docstring.
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: One side of a refspec. Deliberately narrower than git's own rules: no `+`,
#: no `^`, no `~`, no `@{…}`, no glob, no whitespace.
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

#: Where every config-named program is pointed when it has to be named at all.
_NOWHERE = "/nonexistent/factoria-no-exec"

#: Config pinned on every invocation, at command scope, which beats the repo's
#: own for any single-valued key. Each of these is a key whose value git would
#: otherwise *run*. See the module docstring for why, and for the vectors that
#: are closed some other way.
_PINNED: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", _NOWHERE),
    ("core.fsmonitor", "false"),
    ("core.askPass", ""),
    ("core.alternateRefsCommand", "true"),
    # An empty value resets the helper list, so none from any file survives.
    ("credential.helper", ""),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("push.gpgSign", "false"),
    ("log.showSignature", "false"),
    ("gpg.program", _NOWHERE),
    ("gpg.openpgp.program", _NOWHERE),
    ("gpg.ssh.program", _NOWHERE),
    ("gpg.x509.program", _NOWHERE),
    # A submodule is another repository with its own config, which the pins
    # below it cannot see. So git is not allowed to descend into one.
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("push.recurseSubmodules", "no"),
    ("diff.ignoreSubmodules", "dirty"),
)

#: Keys whose names are chosen by the repository — a filter or a URL-scoped
#: credential helper is `<section>.<anything>.<key>` — so they cannot be pinned
#: up front. `_foreign_commands` reads the repo's config and blanks each one.
_FOREIGN_KEYS = re.compile(r"^(filter\..+\.(clean|smudge|process)|credential\..+\.helper)$")

#: The only transports git may use. Set in the environment, which overrides any
#: `protocol.*.allow` in a config file; it is what keeps `ext::<command>` — and
#: every remote helper not on this list — out of reach.
_ALLOWED_PROTOCOLS = "file:git:ssh:http:https"

#: Subcommands that can print a patch, and so reach `diff.external` and
#: `diff.<driver>.textconv`/`.command`. Those cannot be blanked by config — an
#: empty value makes git fail with "cannot run" — so these flags go on instead.
_PATCH_SUBCOMMANDS = frozenset({"diff", "log", "show"})
_NO_DIFF_PROGRAMS = ["--no-ext-diff", "--no-textconv"]

#: Subcommands that start a pack program, which `remote.<name>.uploadpack` /
#: `.receivepack` would otherwise choose — and for which `-c` loses, since git
#: takes the first of several values.
_PACK_FLAG = {"fetch": "--upload-pack", "ls-remote": "--upload-pack", "push": "--receive-pack"}

#: The pack program for a remote that is a path on this machine. Git runs it
#: with the *remote* repository's config and hooks and strips our pinned config
#: on the way — so the pins that matter there travel inside the command itself.
#: `receive.denyCurrentBranch=refuse` stops a push from checking files out in a
#: non-bare remote, which is where that repository's filters would run.
_LOCAL_PACK_PINS = (
    f"-c core.hooksPath={_NOWHERE} -c core.fsmonitor=false "
    "-c core.alternateRefsCommand=true -c receive.denyCurrentBranch=refuse"
)

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
    if subcommand == "fetch":
        _check_fetch(args, rest)
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


def _check_fetch(args: list[str], rest: list[str]) -> None:
    """A fetch is `git fetch [safe flags] <remote-name>`. No refspec, ever.

    The missing refspec is the whole safety property. Without one git uses the
    remote's *configured* refspec, which on any normal clone writes only
    `refs/remotes/<remote>/*` — so this command can move remote-tracking refs
    and nothing else. Spelling a refspec here would let a caller write
    `refs/heads/main` in the operator's own repository, which is the one thing
    a codebase's owner is entitled to assume we never do.
    """
    flags = [item for item in rest if item.startswith("-")]
    positional = [item for item in rest if not item.startswith("-")]
    unknown = [flag for flag in flags if flag not in _FETCH_FLAGS]
    if unknown:
        raise GitDenied(args, f"{unknown[0]} is not an allowed fetch flag")
    if len(positional) != 1:
        raise GitDenied(args, "a fetch must name exactly one remote and no refspec")
    remote = positional[0]
    if not _REMOTE_NAME.match(remote):
        raise GitDenied(
            args,
            f"{remote!r} is not a remote name — a fetch may only read a remote "
            "already configured in the repository, never a URL or a path",
        )


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    # Nothing this process runs may sit waiting for a credential that no human
    # is present to type. A prompt would hang a run until the watchdog fires.
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Both of these beat the repo's config: the first shadows `core.sshCommand`,
    # and an empty proxy command switches `core.gitProxy` off.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new")
    env.setdefault("GIT_PROXY_COMMAND", "")
    env.setdefault("GIT_ALLOW_PROTOCOL", _ALLOWED_PROTOCOLS)
    if extra:
        env.update(extra)
    return env


def _pin(env: dict[str, str], pairs: list[tuple[str, str]]) -> None:
    """Add `pairs` to `env` as command-scope config, after any already there.

    The environment rather than `-c`: `-c` splits at the first `=`, and a
    filter's name is the repository's to choose — `=` included.
    """
    try:
        start = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        start = 0
    for index, (key, value) in enumerate(pairs, start):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_CONFIG_COUNT"] = str(start + len(pairs))


def _foreign_commands(cwd: Path | str | None, env: dict[str, str]) -> list[tuple[str, str]]:
    """Every repo-named command key in the config git will read here, blanked.

    `git config --list` runs nothing, follows `include`s, and sees every scope,
    worktree config included. A config git cannot read is a refusal, not a
    shrug: running the command anyway would run it without these pins.
    """
    argv = ["git", "config", "--list", "-z"]
    try:
        done = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(argv[1:], -1, str(error)) from error
    if done.returncode != 0:
        raise GitError(argv[1:], done.returncode, done.stderr)
    keys = {entry.split("\n", 1)[0] for entry in done.stdout.split("\0") if entry}
    return [(key, "") for key in sorted(keys) if _FOREIGN_KEYS.match(key)]


def _is_local_url(url: str) -> bool:
    """Would git reach `url` through the filesystem? Git's own test, restated."""
    if "://" in url:
        return url.startswith("file://")
    colon, slash = url.find(":"), url.find("/")
    return colon == -1 or (slash != -1 and slash < colon)


def _pack_flag(args: list[str], cwd: Path | str | None) -> str:
    """The explicit pack program for a remote-contacting command."""
    flag = _PACK_FLAG[args[0]]
    program = "git-receive-pack" if args[0] == "push" else "git-upload-pack"
    positional = [item for item in args[1:] if not item.startswith("-")]
    if not positional:
        raise GitDenied(args, "a command that contacts a remote must name it")
    target = positional[0]
    listed = _git(
        ["remote", "get-url", "--all", *(["--push"] if args[0] == "push" else []), target],
        cwd=cwd,
        check=False,
    )
    urls = listed.stdout.split() if listed.returncode == 0 else [target]
    local = {_is_local_url(url) for url in urls}
    if local == {True}:
        return f"{flag}=git {_LOCAL_PACK_PINS} {program.removeprefix('git-')}"
    if True in local:
        raise GitError(
            args,
            -1,
            f"the remote {target!r} lists both a path on this machine and a network "
            "address, and one push cannot be made safe for both. Give each its own "
            "remote.",
        )
    return f"{flag}={program}"


def _git(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run one allowed git command. Synchronous — callers use `to_thread`.

    Every command runs with the pins in `_PINNED`, the repo's own command keys
    blanked, and — where the subcommand can reach one — an explicit diff or
    pack program. This is the one place that happens, so a new caller cannot
    forget it.
    """
    _check(args)
    command = list(args)
    if command[0] in _PATCH_SUBCOMMANDS:
        command[1:1] = _NO_DIFF_PROGRAMS
    command_env = _env(env)
    try:
        if command[0] in _PACK_FLAG:
            command.insert(1, _pack_flag(args, cwd))
        _pin(command_env, [*_PINNED, *_foreign_commands(cwd, command_env)])
    except GitDenied:
        raise
    except GitError as refused:
        # Refused before a child existed, for a reason in the repository rather
        # than in our code. A caller that reads the return code gets it exactly
        # as it would get git's own failure.
        if check:
            raise
        return subprocess.CompletedProcess(["git", *args], refused.code, "", refused.stderr)
    argv = ["git", *command]
    try:
        done = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=command_env,
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


def pending_stat(worktree: Path | str, base: str) -> str:
    """The `--stat` of what `commit_all` is about to commit, before it does.

    Stages everything first, exactly as `commit_all` will — which is why doing
    it early changes nothing — so that new files count. The diff is against
    `base`, not `HEAD`: an agent that committed on its own left work there too,
    and the commit message describes the branch, not the last few files.
    """
    _git(["add", "--all", "."], cwd=worktree)
    done = _git(["diff", "--cached", "--stat", base], cwd=worktree, check=False)
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


def add_detached_worktree(repo: Path | str, path: Path | str, base: str) -> None:
    """`git worktree add --detach <path> <base>` — a checkout on no branch.

    A simulation's worktree (`decisions/0032`). A simulation is discarded
    however it ends, and this module never deletes a ref, not even its own
    (`_check`) — so the only way a discarded run leaves no branch behind is
    never to make one. Removed like any other worktree, by `remove_worktree`.
    """
    _git(["worktree", "add", "--detach", str(path), base], cwd=repo)


def attach_worktree(repo: Path | str, path: Path | str, branch: str) -> None:
    """`git worktree add <path> <branch>` — a checkout of a branch that already
    exists, with no `-b`.

    The restart path's counterpart to `add_worktree` (`decisions/0023`). A run
    that was cancelled or that succeeded has had its worktree removed by the
    cleanup policy while its **branch survives**, holding whatever it committed.
    Restarting a node of that run must go on writing to that branch, so this
    re-attaches a checkout of it rather than branching again — which is what
    `add_worktree` would do, and what fails with "branch already exists".

    Deliberately **not** a fallback inside `add_worktree`. Outside a restart, a
    branch name already taken is a run that should not start, and a helper that
    silently attached instead of refusing would erase that distinction.
    """
    _git(["worktree", "add", str(path), branch], cwd=repo)


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


@dataclass(frozen=True)
class FetchResult:
    """What one `git fetch` did, as data rather than as an exception.

    A fetch that fails is **not** a failed run: a codebase with no remote, or a
    laptop on a train, still has a perfectly good local base branch to cut from.
    So this reports instead of raising, and `worktree.base_ref` decides what the
    failure means — which is how a run stays possible offline while still
    telling the operator, in `summary.md`, that the base it used was whatever
    the clone had last seen.
    """

    ok: bool
    remote: str
    detail: str = ""
    #: True when the repo has no such remote, so nothing was even attempted.
    #: Distinct from a failure: there is nothing here for the operator to fix.
    absent: bool = False


def fetch(repo: Path | str, remote: str = DEFAULT_REMOTE) -> FetchResult:
    """Update `remote`'s remote-tracking refs. Never raises on a bad network.

    This is the one write this module performs on the *operator's own* repo, and
    it is deliberately the narrowest one there is: `_check_fetch` forbids a
    refspec, so the only refs that can move are `refs/remotes/<remote>/*`. No
    local branch, no working tree, no index — a dev with the codebase open in
    their editor sees nothing change.
    """
    if remote_url(repo, remote) is None:
        return FetchResult(ok=False, remote=remote, absent=True, detail="no such remote")
    done = _git(
        ["fetch", "--quiet", "--no-tags", remote],
        cwd=repo,
        check=False,
        timeout=PUSH_TIMEOUT_S,
    )
    if done.returncode == 0:
        return FetchResult(ok=True, remote=remote)
    said = (done.stderr.strip().splitlines() or ["fetch failed"])[-1]
    return FetchResult(ok=False, remote=remote, detail=said)


def default_branch(repo: Path | str, remote: str = DEFAULT_REMOTE) -> str | None:
    """The remote's default branch — what `origin/HEAD` points at — or `None`.

    Read from the clone's own `refs/remotes/<remote>/HEAD`, which `git clone`
    writes once and nothing since has had a reason to move. No network: this is
    asked on the way into every run, and a run must not depend on a forge being
    up to know what "main" is called.
    """
    done = _git(
        ["symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"],
        cwd=repo,
        check=False,
    )
    if done.returncode != 0:
        return None
    name = done.stdout.strip()
    prefix = f"{remote}/"
    return name[len(prefix):] if name.startswith(prefix) else (name or None)


def rev(repo: Path | str, ref: str) -> str | None:
    """The commit `ref` names, or `None` when there is no such ref."""
    done = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo, check=False)
    return done.stdout.strip() or None if done.returncode == 0 else None


def local_branches(repo: Path | str) -> list[str]:
    """Every local branch name, in git's own order."""
    done = _git(
        ["branch", "--list", "--format=%(refname:short)"], cwd=repo, check=False
    )
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


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
