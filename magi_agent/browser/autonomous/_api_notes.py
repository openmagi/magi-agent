"""browser-use API surface notes (Task 0 spike).

Durable record of the ACTUAL public API of the `browser-use` library that the
autonomous BrowserTask tool wraps. Later tasks (provider bridge, engine) MUST
code against the symbol names recorded here, not against guesses.

This module contains NO executable logic. It is plain comments + this docstring.

================================================================================
ENVIRONMENT / VERSIONS (verified locally on 2026-06-09)
================================================================================
  Resolved with: uv sync --extra dev --extra browser
    browser-use == 0.11.13   (extra pins ">=0.2.0"; latest stable resolved)
    playwright   == 1.60.0    (extra pins ">=1.45.0")
  Browser binary: uv run playwright install chromium  (downloads Chromium)
  Python: >=3.11 (repo requires-python)

  Smoke import (PASSES):
    from browser_use import Agent
    from browser_use.llm import ChatAnthropic, ChatOpenAI, ChatGoogle

================================================================================
1. PER-PROVIDER CHAT MODELS  (CONFIRMED)
================================================================================
  Import path (re-exported, stable):  browser_use.llm
    from browser_use.llm import ChatAnthropic, ChatOpenAI, ChatGoogle
  Concrete module locations (for reference; import via browser_use.llm):
    ChatAnthropic -> browser_use.llm.anthropic.chat.ChatAnthropic
    ChatOpenAI    -> browser_use.llm.openai.chat.ChatOpenAI
    ChatGoogle    -> browser_use.llm.google.chat.ChatGoogle
  They are pydantic models implementing browser_use.llm.base.BaseChatModel.
  The Agent `llm=` kwarg is typed `BaseChatModel | None`, so any of the above
  (or a custom BaseChatModel subclass) is accepted.

  Constructor kwargs (selected, all keyword-capable):
    ChatAnthropic(model, max_tokens, temperature, top_p, seed, api_key,
                  auth_token, base_url, timeout, max_retries, default_headers,
                  default_query, http_client, ...)
    ChatOpenAI(model, temperature, frequency_penalty, reasoning_effort, seed,
               service_tier, top_p, api_key, organization, project, base_url,
               timeout, max_retries, max_completion_tokens, ...)
    ChatGoogle(model, temperature, top_p, seed, thinking_budget, thinking_level,
               max_output_tokens, config, max_retries, api_key, vertexai,
               credentials, project, location, http_options, ...)
  -> All three take a positional/keyword `model` (str) + a keyword `api_key`.
     This is what the provider bridge (Task 4) should populate.

================================================================================
2. Agent(...) CONSTRUCTOR  (CONFIRMED)
================================================================================
  Signature head:
    Agent(task: str,
          llm: BaseChatModel | None = None,
          browser_profile=None, browser_session=None, browser=None,
          tools=None, controller=None,
          ...
          register_new_step_callback=None,
          register_done_callback=None,
          register_external_agent_status_raise_error_callback=None,
          register_should_stop_callback=None,
          ...
          use_vision: bool = True,
          max_failures: int = 5,
          max_actions_per_step: int = 5,
          step_timeout: int = 180,
          ...
          **kwargs)

  Answers to the spike questions:
    (a) task string  -> kwarg `task` (positional, type str, REQUIRED)
    (b) LLM model     -> kwarg `llm`  (type BaseChatModel | None, default None)
    (c) per-step callback -> kwarg `register_new_step_callback`
        *** The plan's guess (`register_new_step_callback`) is CORRECT. ***
        Callback type (CONFIRMED from annotations) is either sync or async:
          Callable[[BrowserStateSummary, AgentOutput, int], None]
          | Callable[[BrowserStateSummary, AgentOutput, int], Awaitable[None]]
        i.e. it receives (browser_state_summary, agent_output, step_number:int).
        BrowserStateSummary / AgentOutput live in browser_use.agent.views.

  Other useful callbacks:
    register_done_callback: Callable[[AgentHistoryList], None | Awaitable[None]]
      -- fires once when the run completes.
    register_should_stop_callback / register_external_agent_status_raise_error_callback
      -- cooperative stop / external-status hooks.

  Vision is ON by default (use_vision=True).

================================================================================
3. Agent.run(...)  (CONFIRMED)
================================================================================
  Signature:
    async def run(self,
                  max_steps: int = 500,
                  on_step_start: Callable[[Agent], Awaitable[None]] | None = None,
                  on_step_end:   Callable[[Agent], Awaitable[None]] | None = None,
                 ) -> AgentHistoryList
  -> run() IS a coroutine (await it). `max_steps` IS accepted (default 500).
  -> There are ALSO per-run async step hooks `on_step_start` / `on_step_end`
     (receive the Agent instance) in addition to the constructor callbacks.

  Returns: browser_use.agent.views.AgentHistoryList  (a pydantic model).
  Useful read accessors on the returned history (all CONFIRMED present):
    .is_done()            -> bool
    .is_successful()      -> bool | None      (None if not finished/unknown)
    .final_result()       -> str | None       (final answer/done text)
    .extracted_content()  -> list[str]
    .errors()             -> list[str | None]
    .has_errors()         -> bool
    .urls()               -> list[str | None]  (visited URLs per step)
    .screenshot_paths()   -> list[str | None]
    .number_of_steps()    -> int
    .action_names()       -> list[str]
    .model_actions()      -> list[dict]
    .total_duration_seconds() -> float
    .model_dump() / .model_dump_json()  (pydantic serialization)
    .get_structured_output(output_model) -> structured result if output_model
                                            schema was provided to Agent(...).
  -> Engine (Task 5) should surface final_result() + is_successful() + errors()
     and number_of_steps() back to the BrowserTask handler.

================================================================================
NOTES / GOTCHAS
================================================================================
  - Import everything lazily inside engine.py (default-OFF tool); a clean
    install without `--extra browser` must not break (the smoke test SKIPs).
  - `Agent.run` is async -> the engine must run it on an event loop.
  - The constructor callback `register_new_step_callback` receives
    (BrowserStateSummary, AgentOutput, step:int); the run() hooks receive the
    Agent instance. Pick whichever fits the per-step reporting need.
  - Nothing in this spike was UNVERIFIED: all of the above was read directly
    from the installed browser-use==0.11.13 via inspect.signature.
"""
