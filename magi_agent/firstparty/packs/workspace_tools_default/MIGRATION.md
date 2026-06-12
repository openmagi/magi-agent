# C1 gates decomposition — repeating template for the remaining workspace tools

Status of `Gate5BFullToolHost._handle` branch migration into this pack
(tracked by `tests/firstparty/test_gate5b_c1_boundary.py` — update BOTH
together):

| Tool | Status |
|---|---|
| Clock | MIGRATED (worked example A) |
| Calculation | MIGRATED (worked example A) |
| FileEdit | MIGRATED (worked example B: read-ledger + fuzzy cascade + format-on-write + edit-match receipt) |
| FileRead | pending |
| Glob | pending |
| Grep | pending |
| FileWrite | pending |
| PatchApply | pending |
| Bash | pending |
| TestRun | pending |
| GitDiff | pending |

The dispatch policies (`_enforce_memory_mode` / `_filter_memory_mode_output` /
`_preflight_legacy_tool`) are already migrated to the sibling
`gates_policy_default` pack (`phase = "tool_host"` control_plane entries).
The pure dispatch envelope (counter preflight/dup/budget, allowlist, error
taxonomy, receipts, bounded output, public tool events) stays kernel — never
move it.

## The template (one commit per tool `<T>`)

1. **Re-read the live `_handle` branch for `<T>`**
   (`grep -n 'if tool_name == "<T>"' -A40 magi_agent/gates/gate5b_full_toolhost.py`).
   Map every host attribute it touches to a `WorkspaceHostView` member
   (`magi_agent/packs/context.py`); extend the view ONLY if a kernel mechanism
   is missing. Known mappings:
   - `Bash`/`TestRun`: `view.run_command(command, timeout_s=...)` with
     `view.config.command_timeout_ms / 1000` resp. the module's
     `_TEST_RUN_TIMEOUT_S` (promote the constant or read it lazily).
   - `FileRead`: `view.record_full_read` + port `_handle_file_read` including
     the read-quality path, the did-you-mean/missing-file shape and
     `_did_you_mean_candidates` (needs a view member for safe candidate
     listing, or move the helper to module level first).
   - `GitDiff`: port `_handle_git_diff`; lazy-import the module-level
     `_is_git_repository`, `_git_status_porcelain`, `_git_diff_numstat`
     exactly the way this pack's `_calculation` imports `_evaluate_expression`.
   - `PatchApply`: content-replace arm (ledger + format-on-write via view) and
     the envelope-patch arm (`_apply_envelope_patch` — needs a view member or
     module-level promotion).
2. **Failing test** (append to `tests/firstparty/test_workspace_tools_default_pack.py`):
   same-input dual-host comparison asserting equal `status` +
   `receipt.bounded_output_digest` (for `Bash`/`TestRun` compare
   `exitCode`/`stdout` fields instead — the deadline note is env-dependent).
3. Run -> FAIL (`resolve("<T>") is None`).
4. **MOVE the branch body** into `_<t>(args, view)` + `provide_<t>` in
   `impl.py`; add the `[[provides]]` entry `ref = "workspace:<T>@1"`. Keep
   alias handling (`path`/`filePath`), error strings (`empty_old_text`,
   `unsupported_patch_shape`, ...) and result keys byte-identical.
5. Run -> PASS: the new test + the tool's legacy suite
   (`tests/gates/test_gate5b_ripgrep.py` for Glob/Grep;
   `test_gate5b_format_on_write.py` + `test_gate5b_read_ledger.py` for
   PatchApply; `test_gate5b_shell_env_hygiene.py` for Bash;
   `test_gate5b_test_run.py` for TestRun; `test_gate5b_git_diff.py` for
   GitDiff; `test_gate5b_read_quality.py` + `test_file_tool_path_alias.py` for
   FileRead/FileWrite) + `tests/fixtures/gate5b_golden/` (NEVER
   `capture --write` — C1 is behavior-preserving) +
   `tests/firstparty/test_gate5b_pack_runtime_golden_equivalence.py`.
6. Update the table above + the boundary sets in
   `tests/firstparty/test_gate5b_c1_boundary.py`.
7. Commit: `feat(gates): <T> workspace handler moved to workspace-tools-default pack`.

## Finishing move (only after ALL 11 are migrated)

Delete the legacy `_handle` branches, `_enforce_memory_mode`,
`_filter_memory_mode_output`, `_preflight_legacy_tool` and the dual-load
fallbacks in `_run_dispatch_policies`/`_apply_after_dispatch_policies`; make
the bare `Gate5BFullToolHost` default-load the pack runtime when both seam
kwargs are `None` (the bundle builder already does). The gate5b goldens must
stay byte-identical across the deletion.
