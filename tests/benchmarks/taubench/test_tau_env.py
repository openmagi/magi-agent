# tests/benchmarks/taubench/test_tau_env.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from benchmarks.taubench.episode import EpisodeState
from benchmarks.taubench.tau_env import build_env_function_tools, build_env_tool_callables


@dataclass
class FakeAction:
    name: str
    kwargs: dict


@dataclass
class FakeResp:
    observation: str
    reward: float
    done: bool


@dataclass
class FakeEnv:
    tools_info: tuple = (
        {"type": "function", "function": {"name": "get_order", "description": "d",
         "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}},
    )
    steps: list = field(default_factory=list)

    def step(self, action):
        self.steps.append(action)
        return FakeResp(observation=f"order {action.kwargs.get('id')}", reward=0.0, done=False)


def test_tool_callable_routes_to_env_step_and_records_state() -> None:
    env = FakeEnv()
    state = EpisodeState()
    callables = build_env_tool_callables(env, state=state, action_factory=FakeAction)
    assert set(callables) == {"get_order"}
    out = asyncio.run(callables["get_order"]({"id": "A1"}, None))
    assert "order A1" in str(out)
    assert env.steps[0].name == "get_order"
    assert env.steps[0].kwargs == {"id": "A1"}


def test_function_tool_declaration_exposes_real_params() -> None:
    """The ADK declaration must expose the real parameter name ``id`` nested
    under the ``arguments`` property (the enrichment path), not just at top-level."""
    env = FakeEnv()
    state = EpisodeState()
    tools = build_env_function_tools(env, state=state, action_factory=FakeAction)
    assert len(tools) == 1
    decl = tools[0]._get_declaration()  # type: ignore[attr-defined]
    assert decl is not None, "declaration must not be None"

    params = getattr(decl, "parameters", None)
    assert params is not None, "declaration.parameters must not be None"
    props = getattr(params, "properties", None)
    assert isinstance(props, dict), "declaration.parameters.properties must be a dict"

    # The real param "id" must be reachable nested under the "arguments" property
    # (the enrichment path that _make_enriched patches in).
    arg_schema = props.get("arguments", None)
    assert arg_schema is not None, (
        f"Expected 'arguments' key in declaration properties, got: {list(props.keys())}"
    )
    nested = getattr(arg_schema, "properties", None)
    assert nested is not None and "id" in nested, (
        f"Parameter 'id' not found under arguments.properties. "
        f"Top-level props: {list(props.keys())}, "
        f"nested props: {list(nested.keys()) if nested else None}"
    )


def test_tool_callable_returns_error_observation_on_env_exception() -> None:
    """A tool whose env.step raises must return an error string, not propagate."""
    class BoomEnv:
        tools_info = ({"type": "function", "function": {"name": "boom", "description": "d",
            "parameters": {"type": "object", "properties": {}}}},)

        def step(self, action):
            raise RuntimeError("bad action")

    callables = build_env_tool_callables(BoomEnv(), state=EpisodeState(), action_factory=FakeAction)
    out = asyncio.run(callables["boom"]({}, None))
    assert "Error" in str(out)


from benchmarks.taubench.reliability import ReliabilityConfig, WriteLedger


@dataclass
class _BookEnv:
    tools_info: tuple = (
        {"type": "function", "function": {"name": "book_reservation", "description": "d",
         "parameters": {"type": "object",
            "properties": {
                "flight_type": {"type": "string", "enum": ["one_way", "round_trip"]},
                "user_id": {"type": "string"}},
            "required": ["user_id"]}}},
    )
    steps: list = field(default_factory=list)

    def step(self, action):
        self.steps.append(action)
        return FakeResp(observation="Reservation booked id=R1", reward=0.0, done=False)


def test_l1_blocks_invalid_enum_without_stepping() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(arg_validation=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    out = asyncio.run(callables["book_reservation"]({"user_id": "u1", "flight_type": "one way"}, None))
    assert "flight_type" in str(out)
    assert env.steps == []  # never executed
    assert led.had_successful_write() is False


def test_l3_blocks_duplicate_write() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(dup_write_guard=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    args = {"user_id": "u1", "flight_type": "one_way"}
    out1 = asyncio.run(callables["book_reservation"](dict(args), None))
    assert "booked" in str(out1).lower()
    out2 = asyncio.run(callables["book_reservation"](dict(args), None))
    assert "uplicate" in str(out2)  # "Duplicate write blocked..."
    assert len(env.steps) == 1  # second call never executed


def test_records_successful_write_in_ledger() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(dup_write_guard=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    asyncio.run(callables["book_reservation"]({"user_id": "u1"}, None))
    assert led.had_successful_write() is True


def test_levers_off_by_default_do_not_interfere() -> None:
    env = _BookEnv()
    callables = build_env_tool_callables(env, state=EpisodeState(), action_factory=FakeAction)
    out = asyncio.run(callables["book_reservation"]({"user_id": "u1", "flight_type": "one way"}, None))
    assert "booked" in str(out).lower()  # executed despite invalid enum (validation off)
    assert len(env.steps) == 1


def _spec(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": "d",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}}


@dataclass
class _MultiToolEnv:
    tools_info: tuple = (_spec("book_x"), _spec("cancel_y"), _spec("get_thing"))
    steps: list = field(default_factory=list)

    def step(self, action):
        self.steps.append(action)
        return FakeResp(observation=f"ok {action.name}", reward=0.0, done=False)


def _grounded_callables(env: _MultiToolEnv) -> dict:
    cfg = ReliabilityConfig(grounded_args=True)
    return build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg
    )


def test_grounded_args_first_write_call_prompts_instead_of_executing() -> None:
    env = _MultiToolEnv()
    callables = _grounded_callables(env)
    out1 = asyncio.run(callables["book_x"]({"id": "1"}, None))
    assert "book_x" in str(out1)
    assert env.steps == []  # first call prompted, never executed
    out2 = asyncio.run(callables["book_x"]({"id": "1"}, None))
    assert "ok book_x" in str(out2)
    assert len(env.steps) == 1  # second identical call executed


def test_grounded_args_one_shot_per_tool_name() -> None:
    env = _MultiToolEnv()
    callables = _grounded_callables(env)
    asyncio.run(callables["book_x"]({"id": "1"}, None))  # prompt
    asyncio.run(callables["book_x"]({"id": "1"}, None))  # execute
    out = asyncio.run(callables["cancel_y"]({"id": "2"}, None))
    assert "cancel_y" in str(out)
    assert len(env.steps) == 1  # cancel_y prompted, not executed
    out = asyncio.run(callables["book_x"]({"id": "9"}, None))  # NEW args, same name
    assert "ok book_x" in str(out)
    assert len(env.steps) == 2  # executed immediately, no second prompt


def test_grounded_args_corrected_args_execute_without_second_prompt() -> None:
    env = _MultiToolEnv()
    callables = _grounded_callables(env)
    out1 = asyncio.run(callables["book_x"]({"id": "A"}, None))
    assert "book_x" in str(out1)
    assert env.steps == []
    out2 = asyncio.run(callables["book_x"]({"id": "B"}, None))
    assert "ok book_x" in str(out2)
    assert len(env.steps) == 1
    assert env.steps[0].kwargs == {"id": "B"}  # env receives the corrected args


def test_grounded_args_read_tools_unaffected() -> None:
    env = _MultiToolEnv()
    callables = _grounded_callables(env)
    out = asyncio.run(callables["get_thing"]({"id": "1"}, None))
    assert "ok get_thing" in str(out)
    assert len(env.steps) == 1  # executed immediately, no prompt


def test_grounded_args_off_no_behavior_change() -> None:
    env = _MultiToolEnv()
    callables = build_env_tool_callables(env, state=EpisodeState(), action_factory=FakeAction)
    out = asyncio.run(callables["book_x"]({"id": "1"}, None))
    assert "ok book_x" in str(out)
    assert len(env.steps) == 1  # default config: first write executes immediately
