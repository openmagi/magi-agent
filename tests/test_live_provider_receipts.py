from __future__ import annotations

import json


def test_provider_digest_is_stable_for_equivalent_sanitized_payloads() -> None:
    from openmagi_core_agent.runtime.provider_receipts import (
        provider_digest,
        sanitize_provider_payload,
    )

    left = {
        "text": "safe",
        "path": "/Users/kevin/private/source.txt",
        "Authorization": "Bearer live-token",
        "nested": {"Cookie": "sid=unsafe", "count": 2},
    }
    right = {
        "nested": {"count": 2, "Cookie": "sid=different"},
        "Authorization": "Bearer different-token",
        "path": "/Users/kevin/private/other.txt",
        "text": "safe",
    }

    assert provider_digest(left) == provider_digest(right)
    rendered = json.dumps(sanitize_provider_payload(left), sort_keys=True)
    assert "safe" in rendered
    assert "/Users/kevin" not in rendered
    assert "live-token" not in rendered
    assert "sid=unsafe" not in rendered
    assert "Authorization" not in rendered
    assert "Cookie" not in rendered


def test_provider_receipt_public_fields_are_digest_addressed_and_redacted() -> None:
    from openmagi_core_agent.runtime.provider_receipts import build_provider_receipt

    receipt = build_provider_receipt(
        provider_name="fake-search",
        operation="search",
        status="ok",
        request_payload={
            "query": "safe public query",
            "rawUserText": "do not serialize raw user text",
            "Authorization": "Bearer live-token",
            "filePath": "/workspace/private/input.txt",
        },
        response_payload={
            "result": "safe result",
            "Cookie": "sid=unsafe",
            "downloadPath": "/Users/kevin/private/out.txt",
        },
        duration_ms=42,
        retry_count=2,
        evidence_refs=("evidence://provider/search-1",),
    )

    dumped = receipt.model_dump(by_alias=True)
    rendered = json.dumps(dumped, sort_keys=True)

    assert dumped["providerName"] == "fake-search"
    assert dumped["operation"] == "search"
    assert dumped["status"] == "ok"
    assert dumped["requestDigest"].startswith("sha256:")
    assert dumped["responseDigest"].startswith("sha256:")
    assert dumped["durationMs"] == 42
    assert dumped["retryCount"] == 2
    assert dumped["evidenceRefs"][0].startswith("evidence:")
    assert "evidence://provider/search-1" not in rendered
    assert "safe public query" not in rendered
    assert "safe result" not in rendered
    assert "raw user text" not in rendered.lower()
    assert "live-token" not in rendered
    assert "sid=unsafe" not in rendered
    assert "/workspace/private" not in rendered
    assert "/Users/kevin" not in rendered


def test_direct_provider_receipt_construction_sanitizes_public_identifiers() -> None:
    from openmagi_core_agent.runtime.provider_receipts import ProviderReceipt

    receipt = ProviderReceipt(
        receiptId="/Users/kevin/private/receipt.txt",
        providerName="Alice Example private provider",
        operation="search Alice Example SSN",
        status="ok",
        requestDigest="Bearer live-token",
        responseDigest="/workspace/private/output.txt",
        durationMs=0,
        retryCount=0,
        evidenceRefs=("/Users/kevin/private/evidence.txt",),
    )

    dumped = receipt.model_dump(by_alias=True)
    rendered = json.dumps(dumped, sort_keys=True)
    assert dumped["receiptId"].startswith("provider-receipt:")
    assert dumped["providerName"].startswith("provider:")
    assert dumped["operation"].startswith("operation:")
    assert dumped["requestDigest"].startswith("sha256:")
    assert dumped["responseDigest"].startswith("sha256:")
    assert dumped["evidenceRefs"][0].startswith("evidence:")
    assert "Alice Example" not in rendered
    assert "live-token" not in rendered
    assert "/workspace/private" not in rendered
    assert "/Users/kevin" not in rendered


def test_evidence_refs_and_forged_receipt_paths_are_sanitized() -> None:
    from openmagi_core_agent.runtime.provider_receipts import ProviderReceipt

    receipt = ProviderReceipt(
        receiptId="provider-receipt:safe",
        providerName="fake-search",
        operation="search",
        status="ok",
        requestDigest="sha256:" + "1" * 64,
        responseDigest="sha256:" + "2" * 64,
        evidenceRefs=("evidence://provider/AliceExampleSSN123456789",),
    )
    forged = receipt.model_copy(
        update={
            "receipt_id": "/Users/kevin/private/receipt.txt",
            "provider_name": "Bearer live-token",
            "operation": "search Alice Example private query",
            "request_digest": "Authorization Bearer live-token",
            "response_digest": "/workspace/private/output.txt",
            "evidence_refs": ("/Users/kevin/private/evidence.txt",),
        }
    )
    constructed = ProviderReceipt.model_construct(
        receipt_id="/Users/kevin/private/receipt.txt",
        provider_name="Alice Example private provider",
        operation="search Alice Example private query",
        status="ok",
        request_digest="Bearer live-token",
        response_digest="/workspace/private/output.txt",
        duration_ms=0,
        retry_count=0,
        evidence_refs=("evidence://provider/AliceExampleSSN123456789",),
    )

    rendered = json.dumps(
        [
            receipt.model_dump(by_alias=True),
            forged.model_dump(by_alias=True),
            constructed.model_dump(by_alias=True),
        ],
        sort_keys=True,
    )

    assert receipt.evidence_refs[0].startswith("evidence:")
    assert forged.receipt_id.startswith("provider-receipt:")
    assert constructed.provider_name.startswith("provider:")
    assert "Alice Example" not in rendered
    assert "AliceExampleSSN123456789" not in rendered
    assert "123-45-6789" not in rendered
    assert "live-token" not in rendered
    assert "/workspace/private" not in rendered
    assert "/Users/kevin" not in rendered
