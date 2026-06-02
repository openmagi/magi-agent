from __future__ import annotations

import ast
from pathlib import Path

from openmagi_core_agent.memory.contracts import MemoryRecord, RecallResult
from openmagi_core_agent.memory.policy import MemoryPolicy
from openmagi_core_agent.memory.projection import (
    MemoryRecallRecord,
    SourceAuthorityEnvelope,
    classify_memory_continuity,
    extract_distinctive_phrases,
    project_memory_boundary,
    resolve_source_authority,
    should_retry_stale_memory_promotion,
)


def _record(
    *,
    record_id: str = "root-memory",
    body: str = "Launch plan says keep Telegram onboarding focused.",
    source_ref: str = "memory/ROOT.md",
    visibility: str = "private",
    custom_metadata: dict[str, object] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="bot",
        kind="note",
        body=body,
        source_ref=source_ref,
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility=visibility,
        custom_metadata=custom_metadata or {},
    )


def _recall_result(*records: MemoryRecord) -> RecallResult:
    return RecallResult(
        provider_id="hipocampus-qmd-readonly",
        records=records,
        recall_allowed=True,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=True,
        reason_codes=("fixture",),
    )


def test_prompt_projection_is_disabled_default_off_and_returns_only_boundary_metadata() -> None:
    boundary = project_memory_boundary(
        _recall_result(_record(body="Private memory body must not be injected.")),
        latest_user_text="What is current?",
    )

    assert boundary.prompt_projection_allowed is False
    assert boundary.prompt_text == ""
    assert boundary.diagnostics.prompt_projection_enabled is False
    assert "prompt_projection_disabled" in boundary.diagnostics.reason_codes
    assert boundary.write_intent_allowed is False
    assert boundary.references == ()
    assert "source_authority_disables_long_term_memory" in boundary.diagnostics.reason_codes


def test_source_authority_current_sources_and_classifier_disable_long_term_memory() -> None:
    current_source = resolve_source_authority(
        classifier_policy="normal",
        classifier_current_sources_authoritative=False,
        current_source_kinds=("kb",),
    )
    disabled = resolve_source_authority(
        classifier_policy="disabled",
        classifier_current_sources_authoritative=False,
        current_source_kinds=(),
    )

    assert current_source.long_term_memory_policy == "background_only"
    assert current_source.authority_order[:2] == (
        "L0 latest_user_message",
        "L1 current_turn_sources",
    )
    assert current_source.authority_order.index("L1 current_turn_sources") < current_source.authority_order.index(
        "L4 long_term_memory"
    )
    assert "L0/L1 outrank L4 long-term memory" in current_source.rules

    assert disabled.long_term_memory_policy == "disabled"
    assert "classifier_disabled_long_term_memory" in disabled.reason_codes


def test_source_authority_background_only_keeps_sanitized_refs_without_prompt_injection() -> None:
    authority = resolve_source_authority(
        classifier_policy="background_only",
        classifier_current_sources_authoritative=False,
        current_source_kinds=(),
    )

    boundary = project_memory_boundary(
        _recall_result(_record(body="Old launch plan says option A.")),
        latest_user_text="Use the current uploaded spec.",
        source_authority=authority,
    )

    assert boundary.prompt_projection_allowed is False
    assert boundary.prompt_text == ""
    assert boundary.source_authority.long_term_memory_policy == "background_only"
    assert boundary.references[0].continuity == "background"
    assert "source_authority_background_only" in boundary.diagnostics.reason_codes


def test_incognito_suppresses_prior_long_term_memory_and_read_only_preserves_public_refs() -> None:
    incognito = project_memory_boundary(
        _recall_result(_record()),
        policy=MemoryPolicy(memory_mode="incognito", source_authority="long_term_allowed"),
        latest_user_text="continue the onboarding issue",
    )
    read_only = project_memory_boundary(
        _recall_result(_record()),
        policy=MemoryPolicy(memory_mode="read_only", source_authority="long_term_allowed"),
        latest_user_text="continue the onboarding issue",
        write_intent=True,
    )

    assert incognito.references == ()
    assert incognito.diagnostics.records_input == 1
    assert "incognito_blocks_prior_long_term_memory" in incognito.diagnostics.reason_codes

    assert len(read_only.references) == 1
    assert read_only.write_intent_allowed is False
    assert "read_only_blocks_writes" in read_only.diagnostics.reason_codes


def test_public_boundary_sanitizes_snippets_private_paths_child_prompts_tool_logs_and_reasoning() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body=(
                    "Authorization: Bearer unsafe-token\n"
                    "/Users/kevin/private/repo/secret.txt\n"
                    "/home/kevin/.ssh/id_rsa\n"
                    "/var/lib/kubelet/pods/pod-token\n"
                    "<child_prompt>do hidden child instruction</child_prompt>\n"
                    "<tool_log>{raw args}</tool_log>\n"
                    "<hidden_reasoning>private chain</hidden_reasoning>\n"
                ),
                source_ref="/Users/kevin/private/repo/memory/ROOT.md",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="Summarize memory.",
    )

    ref = boundary.references[0]
    public_dump = ref.model_dump_json(by_alias=True)

    assert "unsafe-token" not in public_dump
    assert "/Users/kevin" not in public_dump
    assert "/home/kevin" not in public_dump
    assert "/var/lib/kubelet" not in public_dump
    assert "child instruction" not in public_dump
    assert "raw args" not in public_dump
    assert "private chain" not in public_dump
    assert ref.source_ref.startswith("memory:")


def test_public_boundary_strips_line_style_raw_payloads_object_refs_and_cookies() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body=(
                    "Visible source summary.\n"
                    "raw_tool_result: Cookie: session=unsafe\n"
                    "chain_of_thought: hidden path /workspace/bot/private.txt\n"
                    "See s3://private-bucket/object?X-Amz-Signature=unsafe\n"
                ),
                source_ref="s3://private-bucket/object?X-Amz-Signature=unsafe",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="Visible source summary",
    )

    encoded = boundary.model_dump_json(by_alias=True)

    assert "Visible source summary" in encoded
    assert "raw_tool_result" not in encoded
    assert "chain_of_thought" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "/workspace/bot" not in encoded
    assert "s3://private-bucket" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert boundary.references[0].source_ref.startswith("memory:")


def test_public_boundary_strips_marker_line_payloads_from_snippets_and_phrases() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body=(
                    "Visible source summary.\n"
                    "private_reasoning:\n"
                    "INTERNAL_REASONING_TEXT\n"
                    "raw_subagent_transcript_secret:\n"
                    "\n"
                    "BLANK_LINE_TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
                    "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK\n"
                    "tool log: SPACE_TOOL_LOG_PAYLOAD_DO_NOT_LEAK\n"
                    "tool args: SPACE_TOOL_ARGS_PAYLOAD_DO_NOT_LEAK\n"
                    "tool result: SPACE_TOOL_RESULT_PAYLOAD_DO_NOT_LEAK\n"
                    "child prompt: SPACE_CHILD_PROMPT_PAYLOAD_DO_NOT_LEAK\n"
                    "hidden reasoning: SPACE_HIDDEN_REASONING_PAYLOAD_DO_NOT_LEAK\n"
                    "private reasoning: SPACE_PRIVATE_REASONING_PAYLOAD_DO_NOT_LEAK\n"
                    "chain of thought: SPACE_CHAIN_OF_THOUGHT_PAYLOAD_DO_NOT_LEAK\n"
                    "private memory: SPACE_PRIVATE_MEMORY_PAYLOAD_DO_NOT_LEAK\n"
                ),
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="Visible source summary",
    )

    encoded = boundary.model_dump_json(by_alias=True)

    assert "Visible source summary" in encoded
    assert "private_reasoning" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "INTERNAL_REASONING_TEXT" not in encoded
    assert "BLANK_LINE_TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK" not in encoded
    assert "SPACE_TOOL_LOG_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_TOOL_ARGS_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_TOOL_RESULT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_CHILD_PROMPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_HIDDEN_REASONING_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_PRIVATE_REASONING_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_CHAIN_OF_THOUGHT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SPACE_PRIVATE_MEMORY_PAYLOAD_DO_NOT_LEAK" not in encoded


def test_public_boundary_strips_space_separated_private_markers_when_first_marker() -> None:
    markers = (
        "tool log",
        "tool args",
        "tool result",
        "child prompt",
        "hidden reasoning",
        "private reasoning",
        "chain of thought",
        "private memory",
    )

    for index, marker in enumerate(markers):
        payload = f"SPACE_MARKER_PAYLOAD_{index}_DO_NOT_LEAK"
        boundary = project_memory_boundary(
            _recall_result(
                _record(
                    body=f"Visible source summary.\n{marker}: {payload}",
                    visibility="public-safe",
                )
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
            latest_user_text="Visible source summary",
        )

        encoded = boundary.model_dump_json(by_alias=True)

        assert "Visible source summary" in encoded
        assert marker not in encoded
        assert payload not in encoded


def test_public_boundary_strips_json_shaped_api_key_snippets_and_phrases() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body='Visible source summary.\n{"api_key": "supersecret123"}',
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="Visible source summary",
    )
    encoded = boundary.model_dump_json(by_alias=True)

    assert "Visible source summary" in encoded
    assert "api_key" not in encoded.casefold()
    assert "supersecret" not in encoded


def test_public_boundary_hashes_plain_https_object_provider_source_refs() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body="Visible object summary.",
                source_ref="https://storage.googleapis.com/private-bucket/object",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="object summary",
    )

    encoded = boundary.model_dump_json(by_alias=True)

    assert boundary.references[0].source_ref.startswith("memory:")
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded


def test_memory_projection_model_copy_cannot_enable_prompt_or_session_injection() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body="Visible public summary.",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="public summary",
    )

    copied = boundary.model_copy(
        update={
            "promptProjectionAllowed": True,
            "promptText": "inject private memory",
            "sessionInjectionAllowed": True,
        }
    )
    constructed = type(boundary).model_construct(
        writeIntentAllowed=False,
        references=(),
        diagnostics=boundary.diagnostics.model_copy(update={"promptProjectionEnabled": True}),
        sourceAuthority=boundary.source_authority,
        promptProjectionAllowed=True,
        promptText="inject private memory",
        sessionInjectionAllowed=True,
    )

    assert copied.prompt_projection_allowed is False
    assert copied.prompt_text == ""
    assert copied.session_injection_allowed is False
    assert constructed.prompt_projection_allowed is False
    assert constructed.prompt_text == ""
    assert constructed.session_injection_allowed is False
    assert constructed.diagnostics.prompt_projection_enabled is False


def test_memory_projection_reference_model_bypasses_are_sanitized() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body="Visible public summary.",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="public summary",
    )

    copied = boundary.model_copy(
        update={
            "references": (
                {
                    "recordId": "forged",
                    "providerId": "hipocampus-qmd-readonly",
                    "sourceRef": (
                        "https://storage.googleapis.com/private-bucket/object?"
                        "X-Amz-Signature=unsafe"
                    ),
                    "scope": "bot",
                    "kind": "note",
                    "confidence": "observed",
                    "visibility": "public-safe",
                    "snippet": (
                        "visible line\n"
                        "raw_tool_result: Cookie: session=unsafe\n"
                        "chain_of_thought: hidden\n"
                        "/workspace/bot/private.txt"
                    ),
                    "continuity": "related",
                    "distinctivePhrases": (
                        "chain_of_thought hidden",
                        "/workspace/bot/private.txt",
                    ),
                    "evidenceRef": (
                        "s3://private-bucket/evidence?X-Amz-Signature=unsafe"
                    ),
                },
            )
        }
    )

    encoded = copied.model_dump_json(by_alias=True)

    assert copied.references[0].source_ref.startswith("memory:")
    assert copied.references[0].snippet == "visible line"
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert "raw_tool_result" not in encoded
    assert "chain_of_thought" not in encoded
    assert "/workspace/bot" not in encoded
    assert "Cookie:" not in encoded


def test_private_and_shared_memory_references_are_ref_only_without_snippets() -> None:
    private = _record(record_id="private", body="private fact must not project")
    shared = _record(record_id="shared", body="shared fact still ref only", visibility="shared")
    public_safe = _record(
        record_id="public",
        body="public safe summary may project",
        visibility="public-safe",
    )

    boundary = project_memory_boundary(
        _recall_result(private, shared, public_safe),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="public safe summary",
    )

    snippets = {ref.record_id: ref.snippet for ref in boundary.references}

    assert snippets["private"] == ""
    assert snippets["shared"] == ""
    assert snippets["public"] == "public safe summary may project"


def test_budget_truncates_explicitly_and_records_diagnostic_metadata() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(record_id="one", body="alpha " * 80, visibility="public-safe"),
            _record(record_id="two", body="beta " * 80, visibility="public-safe"),
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="alpha",
        max_bytes=520,
    )

    assert boundary.diagnostics.truncated is True
    assert boundary.diagnostics.records_input == 2
    assert boundary.diagnostics.records_output <= 2
    assert boundary.diagnostics.bytes_budget == 520
    assert boundary.diagnostics.bytes_used <= boundary.diagnostics.bytes_budget
    assert "budget_truncated" in boundary.diagnostics.reason_codes
    assert any(ref.truncated for ref in boundary.references)


def test_child_isolation_rejects_raw_child_memory_but_accepts_sanitized_child_envelopes() -> None:
    raw_child = _record(
        record_id="child-raw",
        body="raw child transcript says secret",
        source_ref="child/transcripts/raw.jsonl",
        custom_metadata={"childMemoryRaw": True},
    )
    sanitized_child = _record(
        record_id="child-safe",
        body="child evidence summary cites deploy result",
        source_ref="child/envelopes/evidence-1.json",
        custom_metadata={"childEnvelopeSanitized": True, "evidenceRef": "evidence-1"},
    )

    boundary = project_memory_boundary(
        _recall_result(raw_child, sanitized_child),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="deploy result",
    )

    assert [ref.record_id for ref in boundary.references] == ["child-safe"]
    assert boundary.references[0].child_scope == "sanitized_envelope"
    assert "child_raw_memory_rejected" in boundary.diagnostics.reason_codes


def test_root_memory_source_ref_defaults_to_background_without_continuation() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body="Telegram onboarding fix used the provisioning worker.",
                source_ref="memory/ROOT.md",
                custom_metadata={},
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="What about Telegram onboarding?",
    )

    assert boundary.references[0].continuity == "background"


def test_child_isolation_rejects_raw_child_paths_without_metadata() -> None:
    raw_child = _record(
        record_id="child/transcripts/raw.jsonl",
        body="raw child transcript says secret",
        source_ref="child/transcripts/raw.jsonl",
        custom_metadata={},
    )
    sanitized_child = _record(
        record_id="child-safe",
        body="child evidence summary cites deploy result",
        source_ref="child/envelopes/evidence-1.json",
        custom_metadata={"childEnvelopeSanitized": True, "evidenceRef": "evidence-1"},
    )

    boundary = project_memory_boundary(
        _recall_result(raw_child, sanitized_child),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="deploy result",
    )

    assert [ref.record_id for ref in boundary.references] == ["child-safe"]
    assert "child_raw_memory_rejected" in boundary.diagnostics.reason_codes


def test_child_isolated_policy_projects_no_records() -> None:
    boundary = project_memory_boundary(
        _recall_result(_record(body="child isolated memory must not cross boundary")),
        policy=MemoryPolicy(memory_mode="normal", source_authority="child_isolated"),
        latest_user_text="continue child isolated memory",
    )

    assert boundary.references == ()
    assert boundary.diagnostics.records_input == 1
    assert boundary.diagnostics.records_output == 0
    assert "child_memory_scope_isolated" in boundary.diagnostics.reason_codes


def test_metadata_fields_are_sanitized_before_public_projection() -> None:
    authority = resolve_source_authority(
        classifier_policy="normal",
        classifier_current_sources_authoritative=False,
        current_source_kinds=(),
        classifier_reason=(
            "Authorization: Bearer unsafe-token from "
            "/Users/kevin/private/repo/secret.txt\n"
            "private memory:\n"
            "CLASSIFIER_REASON_PAYLOAD_DO_NOT_LEAK\n"
            "tool result: CLASSIFIER_TOOL_RESULT_DO_NOT_LEAK"
        ),
    )
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                visibility="public-safe",
                custom_metadata={
                    "childEnvelopeSanitized": True,
                    "evidenceRef": (
                        "sk-unsafe-secret at /Users/kevin/private/repo/evidence.json\n"
                        "private memory:\n"
                        "EVIDENCE_REF_PAYLOAD_DO_NOT_LEAK\n"
                        "tool result: EVIDENCE_TOOL_RESULT_DO_NOT_LEAK"
                    ),
                },
            )
        ),
        latest_user_text="launch plan",
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        source_authority=authority,
    )

    public_dump = boundary.model_dump_json(by_alias=True)

    assert "unsafe-token" not in public_dump
    assert "sk-unsafe-secret" not in public_dump
    assert "/Users/kevin" not in public_dump
    assert "private memory" not in public_dump
    assert "tool result" not in public_dump
    assert "CLASSIFIER_REASON_PAYLOAD_DO_NOT_LEAK" not in public_dump
    assert "CLASSIFIER_TOOL_RESULT_DO_NOT_LEAK" not in public_dump
    assert "EVIDENCE_REF_PAYLOAD_DO_NOT_LEAK" not in public_dump
    assert "EVIDENCE_TOOL_RESULT_DO_NOT_LEAK" not in public_dump
    assert boundary.source_authority.classifier_reason
    assert boundary.references[0].evidence_ref


def test_prebuilt_source_authority_and_reason_codes_are_sanitized_before_public_projection() -> None:
    unsafe_authority = SourceAuthorityEnvelope.model_construct(
        schemaVersion="sourceAuthorityEnvelope.v1",
        currentSourceKinds=(),
        longTermMemoryPolicy="normal",
        classifierPolicy="normal",
        classifierCurrentSourcesAuthoritative=False,
        classifierReason=(
            "private memory:\n"
            "CLASSIFIER_REASON_PAYLOAD_DO_NOT_LEAK\n"
            "tool result: CLASSIFIER_TOOL_RESULT_DO_NOT_LEAK"
        ),
        authorityOrder=("L0 latest_user_message",),
        rules=("safe rule",),
        reasonCodes=(
            "raw_subagent_transcript_secret:\n"
            "AUTHORITY_REASON_PAYLOAD_DO_NOT_LEAK\n"
            "tool result: AUTHORITY_TOOL_RESULT_DO_NOT_LEAK",
        ),
    )
    recall = RecallResult(
        provider_id="hipocampus-qmd-readonly",
        records=(_record(visibility="public-safe"),),
        recall_allowed=True,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=True,
        reason_codes=(
            "raw_subagent_transcript_secret:\n"
            "DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK\n"
            "tool result: DIAGNOSTIC_TOOL_RESULT_DO_NOT_LEAK",
        ),
    )

    boundary = project_memory_boundary(
        recall,
        latest_user_text="launch plan",
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        source_authority=unsafe_authority,
    )
    public_dump = boundary.model_dump_json(by_alias=True)

    assert "private memory" not in public_dump
    assert "raw_subagent_transcript" not in public_dump
    assert "tool result" not in public_dump
    assert "CLASSIFIER_REASON_PAYLOAD_DO_NOT_LEAK" not in public_dump
    assert "CLASSIFIER_TOOL_RESULT_DO_NOT_LEAK" not in public_dump
    assert "AUTHORITY_REASON_PAYLOAD_DO_NOT_LEAK" not in public_dump
    assert "AUTHORITY_TOOL_RESULT_DO_NOT_LEAK" not in public_dump
    assert "DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK" not in public_dump
    assert "DIAGNOSTIC_TOOL_RESULT_DO_NOT_LEAK" not in public_dump
    assert boundary.source_authority.classifier_reason == "[redacted-metadata]"
    assert any(code.startswith("[redacted") for code in boundary.diagnostics.reason_codes)


def test_source_authority_envelope_construct_and_copy_sanitize_public_strings() -> None:
    authority = SourceAuthorityEnvelope.model_construct(
        schemaVersion="sourceAuthorityEnvelope.v1",
        currentSourceKinds=("private memory:\nCURRENT_KIND_PAYLOAD_DO_NOT_LEAK",),
        longTermMemoryPolicy="normal",
        classifierPolicy="normal",
        classifierCurrentSourcesAuthoritative=False,
        classifierReason="private memory:\nCONSTRUCT_REASON_PAYLOAD_DO_NOT_LEAK",
        authorityOrder=("private memory:\nAUTHORITY_ORDER_PAYLOAD_DO_NOT_LEAK",),
        rules=("tool result: RULE_PAYLOAD_DO_NOT_LEAK",),
        reasonCodes=("tool result: CONSTRUCT_REASON_CODE_DO_NOT_LEAK",),
    )
    copied = authority.model_copy(
        update={
            "classifierReason": "private memory:\nCOPY_REASON_PAYLOAD_DO_NOT_LEAK",
            "reasonCodes": ("tool result: COPY_REASON_CODE_DO_NOT_LEAK",),
        }
    )

    encoded = authority.model_dump_json(by_alias=True) + copied.model_dump_json(by_alias=True)

    assert "private memory" not in encoded
    assert "tool result" not in encoded
    assert "CURRENT_KIND_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "AUTHORITY_ORDER_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "RULE_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "CONSTRUCT_REASON_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "CONSTRUCT_REASON_CODE_DO_NOT_LEAK" not in encoded
    assert "COPY_REASON_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COPY_REASON_CODE_DO_NOT_LEAK" not in encoded
    assert authority.classifier_reason == "[redacted-metadata]"
    assert copied.classifier_reason == "[redacted-metadata]"


def test_public_boundary_sanitizes_reference_record_and_provider_ids() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            MemoryRecord(
                id="/Users/kevin/.ssh/id_rsa",
                scope="bot",
                kind="note",
                body="Visible source summary.",
                source_ref="memory/ROOT.md",
                provider_id="sk-live-secretprovider12345",
                confidence="observed",
                visibility="public-safe",
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="Visible source summary",
    )
    copied = boundary.references[0].model_copy(
        update={
            "recordId": "raw_subagent_transcript_secret_RECORD_PAYLOAD_DO_NOT_LEAK",
            "providerId": "raw_subagent_transcript_secret_PROVIDER_PAYLOAD_DO_NOT_LEAK",
            "sourceRef": "raw_subagent_transcript_secret_SOURCE_PAYLOAD_DO_NOT_LEAK",
        }
    )

    encoded = boundary.model_dump_json(by_alias=True) + copied.model_dump_json(by_alias=True)

    assert "/Users/kevin" not in encoded
    assert "id_rsa" not in encoded
    assert "sk-live-secretprovider12345" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "RECORD_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "PROVIDER_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "SOURCE_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert boundary.references[0].record_id.startswith("memory:")
    assert boundary.references[0].provider_id.startswith("provider:")
    assert copied.record_id.startswith("memory:")
    assert copied.provider_id.startswith("provider:")
    assert copied.source_ref.startswith("memory:")


def test_root_source_ref_overrides_provider_controlled_recall_source_metadata() -> None:
    boundary = project_memory_boundary(
        _recall_result(
            _record(
                body="Telegram onboarding fix used the provisioning worker.",
                source_ref="memory/ROOT.md",
                custom_metadata={"recallSource": "qmd"},
            )
        ),
        policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        latest_user_text="What about Telegram onboarding?",
    )

    assert boundary.references[0].continuity == "background"


def test_tokenization_normalizes_to_nfc_before_overlap() -> None:
    assert (
        classify_memory_continuity(
            latest_user_text="café",
            memory_text="cafe\u0301",
            source="qmd",
        )
        == "related"
    )


def test_continuity_helpers_match_active_related_background_and_stale_promotion_policy() -> None:
    assert (
        classify_memory_continuity(
            latest_user_text="Continue the Telegram onboarding fix.",
            memory_text="Telegram onboarding fix used the provisioning worker.",
            source="qmd",
        )
        == "active"
    )
    assert (
        classify_memory_continuity(
            latest_user_text="What about Telegram onboarding?",
            memory_text="Telegram onboarding fix used the provisioning worker.",
            source="qmd",
        )
        == "related"
    )
    assert (
        classify_memory_continuity(
            latest_user_text="What should we do now?",
            memory_text="Telegram onboarding fix used the provisioning worker.",
            source="root",
        )
        == "background"
    )

    phrases = extract_distinctive_phrases("Telegram onboarding fix used the provisioning worker.")
    decision = should_retry_stale_memory_promotion(
        latest_user_text="What should we do now?",
        assistant_text="Should we choose the Telegram onboarding fix?",
        records=(
            MemoryRecallRecord(
                turn_id="turn-1",
                source="qmd",
                path="memory/ROOT.md",
                continuity="background",
                distinctive_phrases=phrases,
            ),
        ),
    )

    assert decision.retry is True
    assert decision.reason == "background memory phrase promoted into decision request"


def test_projection_module_keeps_import_boundary_free_of_live_runtime_surfaces() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "openmagi_core_agent"
        / "memory"
        / "projection.py"
    ).read_text()
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "google.adk",
        "openmagi_core_agent.transport.routes",
        "openmagi_core_agent.deploy",
        "openmagi_core_agent.frontend",
        "openmagi_core_agent.db",
        "openmagi_core_agent.memory.adapters",
        "openmagi_core_agent.services",
    )
    forbidden_exact = {
        "subprocess",
        "socket",
        "http.client",
        "httpx",
        "requests",
        "urllib",
        "urllib.request",
    }

    assert not any(module in forbidden_exact for module in imported_modules)
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    forbidden_source_fragments = (
        "__import__(",
        "importlib.import_module",
        "socket.",
        "urllib.",
        "http.client",
        "requests.",
        "httpx.",
    )
    for fragment in forbidden_source_fragments:
        assert fragment not in source
    assert "qmd" not in source.lower()
    assert "hipocampus" not in source.lower()
