"""Turn-API adapters: the magi-cp seam.

A ``TurnApiAdapter`` drives one conversational surface over an in-process
``TestClient`` and normalizes every turn to a common ``TurnResult`` so
scenarios, oracles, user-sims, and reports are adapter-agnostic. Two magi-agent
implementations ship in v1:

- ``MagiRuleFlowAdapter``: route A (single-rule NL compiler) + route E save.
- ``MagiPolicyFlowAdapter``: route B (producer+gate policy compiler) + route C
  from-plan save.

Each adapter owns the wire echo contract (history append; ``draft_so_far`` vs
``paramsSoFar`` naming) so scenario authors never see wire naming. A future
``MagiCpAdapter`` implements the same protocol against magi-cp with no change to
anything downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_ROUTE_A = "/v1/app/customize/custom-rules/compile-interactive"
_ROUTE_B = "/v1/app/policies/compile/interactive"
_ROUTE_SAVE_RULE = "/v1/app/customize/custom-rules"
_ROUTE_FROM_PLAN = "/v1/app/policies/from-plan"


@dataclass
class TurnResult:
    """One normalized conversational turn (flow-agnostic)."""

    assistant_message: str
    #: The draft (flow A) or params (flow B) working state, always a dict.
    working: dict[str, Any]
    plan: dict[str, Any] | None
    missing: list[str]
    questions: list[dict[str, Any]]
    needs_more: bool
    ready_to_save: bool
    schema_issues: list[Any]
    raw: dict[str, Any]
    http_status: int


@dataclass
class SaveResult:
    """Outcome of the save leg."""

    ok: bool
    http_status: int
    raw: dict[str, Any]
    #: Populated on a from-plan save (route C).
    policy_id: str | None = None
    producer_id: str | None = None
    gate_id: str | None = None
    #: Populated on an envelope save (route E).
    rule_id: str | None = None


@dataclass
class TurnState:
    """Per-scenario conversation state the adapter threads across turns."""

    history: list[dict[str, str]] = field(default_factory=list)
    #: Last server-returned working state, echoed verbatim next turn.
    working: dict[str, Any] = field(default_factory=dict)
    #: Last server-returned plan (flow B only).
    plan: dict[str, Any] | None = None


@dataclass
class PersistedSnapshot:
    """A read of the persisted authoring state after (or during) a scenario."""

    #: Raw customize.json store contents (``{}`` if the file is absent).
    store: dict[str, Any]
    #: ``GET /v1/app/policies`` body.
    policies: dict[str, Any]
    #: ``GET /v1/app/customize`` body (triggers the U2 backfill).
    customize: dict[str, Any]
    #: sha256 of the raw customize.json bytes (b"" when absent).
    store_hash: str


@runtime_checkable
class TurnApiAdapter(Protocol):
    """The seam every tier drives; magi-agent and magi-cp both implement it."""

    flow: str

    def start(self, scenario: Any) -> TurnState: ...

    def step(self, state: TurnState, say: str | None, answers: dict[str, str]) -> TurnResult: ...

    def save(self, state: TurnState, scenario: Any) -> SaveResult: ...

    def snapshot_persisted(self) -> PersistedSnapshot: ...


# ---------------------------------------------------------------------------
# Shared magi-agent adapter base
# ---------------------------------------------------------------------------


def _store_bytes(path) -> bytes:
    try:
        return path.read_bytes()
    except (FileNotFoundError, OSError):
        return b""


class _MagiAdapterBase:
    """Shared TestClient plumbing for the two magi-agent adapters.

    Store isolation is per-adapter: each instance points ``MAGI_CUSTOMIZE`` at
    its own tmp file for the lifetime of its ``TestClient`` (via an env patch
    applied before ``create_app`` — the caller sets ``MAGI_CUSTOMIZE`` in the
    environment; the runner allocates a fresh tmp dir per scenario). We resolve
    the concrete store path from the env at construction so ``snapshot_persisted``
    can hash the bytes directly.
    """

    def __init__(self, runtime: Any, token: str) -> None:
        import os

        from fastapi.testclient import TestClient

        from magi_agent.app import create_app

        self._token = token
        self._client = TestClient(create_app(runtime))
        self._client.headers.update({"x-gateway-token": token})
        store = os.environ.get("MAGI_CUSTOMIZE")
        self._store_path = None
        if store:
            from pathlib import Path

            self._store_path = Path(store)

    def snapshot_persisted(self) -> PersistedSnapshot:
        import hashlib
        import json

        raw = b""
        store: dict[str, Any] = {}
        if self._store_path is not None:
            raw = _store_bytes(self._store_path)
            if raw:
                try:
                    store = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    store = {}
        # GET /customize runs the idempotent backfill; do it BEFORE hashing so
        # the snapshot reflects the same store the catalog oracles read.
        customize_resp = self._client.get("/v1/app/customize")
        customize = customize_resp.json() if customize_resp.status_code == 200 else {}
        policies_resp = self._client.get("/v1/app/policies")
        policies = policies_resp.json() if policies_resp.status_code == 200 else {}
        # Re-read after the GET so orphan/backfill oracles see post-backfill bytes.
        if self._store_path is not None:
            raw = _store_bytes(self._store_path)
            if raw:
                try:
                    store = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    pass
        store_hash = hashlib.sha256(raw).hexdigest() if raw else ""
        return PersistedSnapshot(
            store=store,
            policies=policies,
            customize=customize,
            store_hash=store_hash,
        )

    def store_hash(self) -> str:
        import hashlib

        return hashlib.sha256(_store_bytes(self._store_path)).hexdigest() if self._store_path else ""


# ---------------------------------------------------------------------------
# Flow A: single-rule NL compiler (route A + save route E)
# ---------------------------------------------------------------------------


class MagiRuleFlowAdapter(_MagiAdapterBase):
    flow = "single_rule"

    def start(self, scenario: Any) -> TurnState:
        return TurnState()

    def step(
        self, state: TurnState, say: str | None, answers: dict[str, str]
    ) -> TurnResult:
        if say is not None:
            state.history.append({"role": "user", "content": say})
        payload = {
            "history": state.history,
            "draft_so_far": state.working,
            "answers": answers or {},
        }
        resp = self._client.post(_ROUTE_A, json=payload)
        body = resp.json() if _has_json(resp) else {}
        working = body.get("draft") or {}
        # Echo the server draft verbatim next turn (client never mutates it).
        if resp.status_code == 200 and body.get("draft") is not None:
            state.working = body["draft"]
        return TurnResult(
            assistant_message=body.get("assistant_message", ""),
            working=working,
            plan=None,
            missing=list(body.get("missing_fields") or []),
            questions=list(body.get("questions") or []),
            needs_more=bool(body.get("needs_more")),
            ready_to_save=bool(body.get("ready_to_save")),
            schema_issues=list(body.get("schema_issues") or []),
            raw=body,
            http_status=resp.status_code,
        )

    def save(self, state: TurnState, scenario: Any) -> SaveResult:
        # Route E envelope save: draft + displayName? + intent (first user turn).
        intent = None
        for h in state.history:
            if h.get("role") == "user" and str(h.get("content") or "").strip():
                intent = str(h["content"]).strip()
                break
        rule_body: dict[str, Any] = dict(state.working)
        save_spec = _save_spec(scenario)
        if intent is not None:
            rule_body["intent"] = intent
        display_name = save_spec.get("display_name") if save_spec else None
        if display_name:
            rule_body["displayName"] = display_name
        resp = self._client.put(_ROUTE_SAVE_RULE, json=rule_body)
        body = resp.json() if _has_json(resp) else {}
        return SaveResult(
            ok=resp.status_code == 200,
            http_status=resp.status_code,
            raw=body,
            rule_id=body.get("id"),
        )


# ---------------------------------------------------------------------------
# Flow B: producer+gate policy compiler (route B + save route C)
# ---------------------------------------------------------------------------


class MagiPolicyFlowAdapter(_MagiAdapterBase):
    flow = "linked_policy"

    def start(self, scenario: Any) -> TurnState:
        return TurnState()

    def step(
        self, state: TurnState, say: str | None, answers: dict[str, str]
    ) -> TurnResult:
        if say is not None:
            state.history.append({"role": "user", "content": say})
        payload = {
            "history": state.history,
            "paramsSoFar": state.working,
            "answers": answers or {},
        }
        resp = self._client.post(_ROUTE_B, json=payload)
        body = resp.json() if _has_json(resp) else {}
        working = body.get("params") or {}
        if resp.status_code == 200 and body.get("params") is not None:
            state.working = body["params"]
        state.plan = body.get("plan")
        return TurnResult(
            assistant_message=body.get("assistant_message", ""),
            working=working,
            plan=body.get("plan"),
            missing=list(body.get("missing_params") or []),
            questions=list(body.get("questions") or []),
            needs_more=bool(body.get("needs_more")),
            ready_to_save=bool(body.get("ready_to_save")),
            schema_issues=list(body.get("schema_issues") or []),
            raw=body,
            http_status=resp.status_code,
        )

    def save(self, state: TurnState, scenario: Any) -> SaveResult:
        # Route C from-plan save: persist the returned plan.
        resp = self._client.post(_ROUTE_FROM_PLAN, json={"plan": state.plan})
        body = resp.json() if _has_json(resp) else {}
        return SaveResult(
            ok=resp.status_code == 200 and bool(body.get("ok")),
            http_status=resp.status_code,
            raw=body,
            policy_id=body.get("policyId"),
            producer_id=body.get("producerId"),
            gate_id=body.get("gateId"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_json(resp: Any) -> bool:
    try:
        resp.json()
        return True
    except ValueError:
        return False


def _save_spec(scenario: Any) -> dict[str, Any] | None:
    """Best-effort read of a scenario's save spec (dict or dataclass)."""
    if scenario is None:
        return None
    spec = getattr(scenario, "save_spec", None)
    if isinstance(spec, dict):
        return spec
    if isinstance(scenario, dict):
        s = scenario.get("save_spec")
        if isinstance(s, dict):
            return s
    return None
