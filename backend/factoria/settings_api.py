"""The settings surface: agent account, sign-in, and git access.

This is the second face of the CLI subprocess layer (`DESIGN.md`, *The Claude
CLI is the one runtime dependency*) — the same supervisor pointed at different
subcommands. The frontend never executes anything itself; it drives these
invocations, which is what lets a browser tab sign the container in.

**Sign-in is polled, not streamed.** SSE is stage 5's, and a login has perhaps
four state changes over a minute of human time — a `GET` every second is less
machinery than a second event transport, and it reconnects for free when the
operator's tab sleeps. The state is server-side and single-flighted, so polling
from two tabs shows both the same flow rather than starting a second.

**Scrubbing.** Everything returned from here that came from a subprocess passes
through `scrub.scrub_text` first — this is one of the two boundaries the design
names. The single exception is the verification URL, which carries a PKCE
challenge by design and is the entire payload of the sign-in flow; it goes to
the tab that asked and is never logged or persisted.

Mutating routes are `POST` (never `GET`), so the Origin check in
`security.py` covers every one of them. A `GET /api/agent/login` that *started*
a login would be a sign-in any page in the operator's browser could trigger.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from . import config, sshkeys
from .agent import cli, login
from .agent.fake import FAKE_MODEL
from .scrub import scrub_text

router = APIRouter(prefix="/api")


class CodeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: `<code>#<state>`, exactly as the provider showed it. Reaches the child's
    #: stdin verbatim — see `agent/login.py` for why the `#` must survive.
    code: str = Field(min_length=1, max_length=4096)


class ModelOffer(BaseModel):
    """What one provider can be asked to run on — everything a model picker
    needs, and nothing about whether the CLI is healthy.

    It exists because there is more than one provider and only one of them is a
    CLI. A picker fed `handshake.models` offers five Claude ids for a node a
    fake will drive, and labels its inherit option with a model that run will
    never report — which is not a rendering bug the browser can fix, because
    nothing on that side knows what an adapter honours.

    `fixed` is that knowledge, stated once here rather than as a
    `provider === "fake"` test in every picker: the provider reports its own id
    whatever it is handed, so a control offering a choice is lying. What the UI
    does about it — grey it, say so — stays the UI's.
    """

    model_config = ConfigDict(extra="forbid")

    models: list[cli.Model] = Field(default_factory=list)
    #: None when the provider could not be asked. Never a hardcoded fallback,
    #: for `cli.Handshake.default_model`'s reason.
    default_model: str | None = None
    default_model_label: str | None = None
    fixed: bool = False


def _fake_offer() -> ModelOffer:
    """The fake's own answer, read from the fake rather than written here.

    One model, which is also its default, and `fixed`: `FakeRun` keeps only the
    session id off a `RunConfig` and reports `FAKE_MODEL` whatever a node asked
    for (`agent/fake.py`). A list of one is the honest shape — there is exactly
    one thing a dry run can report — and it keeps the id in the single file
    that defines it instead of copying it into the browser.
    """
    return ModelOffer(
        models=[
            cli.Model(
                value=FAKE_MODEL,
                label=FAKE_MODEL,
                detail="What a dry run reports. Scripted, and it spends nothing.",
                resolved=FAKE_MODEL,
            )
        ],
        default_model=FAKE_MODEL,
        default_model_label=FAKE_MODEL,
        fixed=True,
    )


class AgentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: cli.AccountStatus
    handshake: cli.Handshake
    #: What a run uses when neither the node nor the project names a provider —
    #: `FACTOR_IA_ADAPTER`, normalized. An environment fact the browser has no
    #: other way to learn, and the settings panel names it rather than saying
    #: "the container default" and leaving the operator to guess which.
    default_provider: str
    #: What each provider offers, keyed by the name a node's `adapter` and a
    #: project's `default_provider` are spelled with. Every provider that
    #: exists is on every response, so a picker walks the ladder and reads one
    #: entry rather than asking again when the answer changes.
    providers: dict[str, ModelOffer] = Field(default_factory=dict)


def _scrubbed(account: cli.AccountStatus) -> cli.AccountStatus:
    return account.model_copy(update={"detail": scrub_text(account.detail)})


@router.get("/agent/status", response_model=AgentStatus)
async def agent_status() -> AgentStatus:
    """Account status plus what the CLI says it offers.

    Two subprocess calls, both free of tokens and both answering while signed
    out — which is what makes the default model readable before the operator
    has ever signed in.
    """
    account = _scrubbed(await cli.auth_status())
    handshake = await cli.handshake()
    # Coerced the same way `orchestrator.adapter_name_for` coerces it, so the
    # panel cannot show a third value that no run would ever resolve to.
    name = config.adapter_name()
    return AgentStatus(
        account=account,
        handshake=handshake,
        default_provider="fake" if name == "fake" else "claude",
        # `handshake` stays the CLI's own answer — the settings screen reads it
        # to say whether the CLI is reachable, which is not a question about
        # models. This is that same answer shaped for the pickers, alongside
        # the provider the CLI knows nothing about.
        providers={
            "claude": ModelOffer(
                models=handshake.models,
                default_model=handshake.default_model,
                default_model_label=handshake.default_model_label,
            ),
            "fake": _fake_offer(),
        },
    )


@router.get("/agent/login", response_model=login.LoginState)
async def read_login() -> login.LoginState:
    """The current sign-in flow, for polling. Never starts one."""
    return login.login_state()


@router.post("/agent/login", response_model=login.LoginState)
async def begin_login() -> login.LoginState:
    """Start a sign-in, or join the one already in progress (single-flight)."""
    return await login.start_login()


@router.post("/agent/login/code", response_model=login.LoginState)
async def submit_login_code(body: CodeSubmission) -> login.LoginState:
    # `async` deliberately: FastAPI runs a plain `def` handler in a worker
    # thread, and writing to the child's stdin transport from off the event
    # loop is not safe. This one touches a live subprocess.
    return login.submit_code(body.code)


@router.post("/agent/login/cancel", response_model=login.LoginState)
async def cancel_login() -> login.LoginState:
    return await login.cancel_login()


@router.get("/ssh/key", response_model=sshkeys.SshKeyStatus)
async def read_ssh_key() -> sshkeys.SshKeyStatus:
    """The **public** half and its fingerprint. There is no private counterpart
    to this route, here or anywhere — see `sshkeys.py`."""
    return await sshkeys.status()


@router.post(
    "/ssh/key", response_model=sshkeys.SshKeyStatus, status_code=status.HTTP_201_CREATED
)
async def create_ssh_key() -> sshkeys.SshKeyStatus:
    try:
        return await sshkeys.generate()
    except sshkeys.KeyExists as exc:
        # Regenerating would silently orphan every forge the old key is
        # enrolled in. Deleting the key is a deliberate act with no UI.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
