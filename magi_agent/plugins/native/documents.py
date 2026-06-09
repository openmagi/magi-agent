from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping

from magi_agent.artifacts.file_delivery import (
    FileDeliveryBoundary,
    FileDeliveryConfig,
    FileDeliveryRequest,
)
from magi_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef
from magi_agent.plugins.native._common import blocked_result, digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.tools.spreadsheet_tools import csv_write
from magi_agent.web_acquisition.policy import redact_public_text


def document_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    content = str(arguments.get("content") or arguments.get("text") or "")
    if not content.strip():
        return blocked_result("DocumentWrite", "content_required")
    path_value = arguments.get("path") or arguments.get("filename") or "magi-document.md"
    try:
        path = safe_child_path(context, path_value, default_name="magi-document.md")
    except ValueError as error:
        return blocked_result("DocumentWrite", str(error))
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_content = redact_public_text(content, max_chars=200_000)
    path.write_text(safe_content, encoding="utf-8")
    relative = path.relative_to(safe_child_path(context, ".", default_name=".")).as_posix()
    return ok_result(
        "DocumentWrite",
        {
            "path": relative,
            "pathRef": relative,
            "contentDigest": digest(safe_content),
            "byteCount": len(safe_content.encode("utf-8")),
            "localOnly": True,
        },
    )


def spreadsheet_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    args = dict(arguments)
    args.setdefault("path", "magi-spreadsheet.csv")
    if "rows" not in args:
        args["rows"] = [["value"], [str(args.get("content") or "")]]
    return csv_write(args, context)


class _LocalFakeFileArtifactProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, artifact_ref: str, content_digest: str) -> None:
        self._artifact_ref = artifact_ref
        self._content_digest = content_digest

    def write_artifact(self, request: FileDeliveryRequest) -> Mapping[str, object]:
        return {
            "status": "ok",
            "artifactRef": self._artifact_ref,
            "contentDigest": self._content_digest,
            "receiptId": f"artifact-receipt:{_short_digest(request.request_id)}",
        }


class _LocalFakeChannelDeliveryProvider:
    openmagi_local_fake_provider = True

    def deliver(self, request: FileDeliveryRequest) -> ChannelDeliveryReceipt:
        if request.channel is None:
            raise ValueError("channel_required")
        return ChannelDeliveryReceipt(
            receiptId=f"receipt:{_short_digest(request.request_id)}",
            requestId=request.request_id,
            channel=request.channel,
            status="sent",
            providerMessageId=f"message:{_short_digest(request.request_id + ':message')}",
            artifactRefs=request.artifact_refs,
            fileRefs=request.file_refs,
        )


def file_deliver(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _file_delivery_result("FileDeliver", "file.deliver", arguments, context)


def file_send(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _file_delivery_result("FileSend", "file.send", arguments, context)


def _file_delivery_result(
    tool_name: str,
    operation: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> ToolResult:
    path_value = arguments.get("path") or arguments.get("filename")
    if not path_value:
        return blocked_result(tool_name, "path_required")
    try:
        path = safe_child_path(context, path_value, default_name="magi-document.md", mutating=False)
    except ValueError as error:
        return blocked_result(tool_name, str(error))
    if not path.exists() or not path.is_file():
        return blocked_result(tool_name, "file_not_found")
    try:
        content = path.read_bytes()
    except OSError:
        return blocked_result(tool_name, "file_read_failed")

    content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    artifact_ref = _artifact_ref(arguments, content_digest)
    file_ref = f"file:{hashlib.sha1(content_digest.encode('utf-8')).hexdigest()[:16]}"
    request_id = _request_id(tool_name, context, content_digest)
    request = FileDeliveryRequest(
        operation=operation,
        requestId=request_id,
        sessionKey=_session_key(context),
        channel=_channel_ref(arguments, context),
        artifactRefs=(artifact_ref,),
        fileRefs=(file_ref,),
        filename=path.name,
        mimeType=_mime_type(arguments, path.name),
        contentDigest=content_digest,
        metadata={
            "toolName": tool_name,
            "localOnly": True,
        },
    )
    decision = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        request,
        artifact_provider=_LocalFakeFileArtifactProvider(
            artifact_ref=artifact_ref,
            content_digest=content_digest,
        ),
        channel_provider=_LocalFakeChannelDeliveryProvider(),
    )
    projection = decision.public_projection()
    if decision.status not in ("delivered_local_fake", "delivered_live") or decision.delivery_receipt is None:
        reason = decision.reason_codes[0] if decision.reason_codes else "file_delivery_blocked"
        return blocked_result(tool_name, reason)

    output_digest = digest(projection)
    return ToolResult(
        status="ok",
        output=projection,
        llmOutput=projection,
        transcriptOutput={
            "toolName": tool_name,
            "outputDigest": output_digest,
            "deliveryReceiptDigest": digest(decision.delivery_receipt.model_dump(by_alias=True)),
        },
        artifactRefs=(decision.artifact_ref,) if decision.artifact_ref is not None else (),
        fileRefs=(file_ref,),
        deliveryReceipts=(decision.delivery_receipt.receipt_id,),
        metadata={
            "toolName": tool_name,
            "handler": "first_party_native_local",
            "outputDigest": output_digest,
            "deliveryReceiptDigest": digest(decision.delivery_receipt.model_dump(by_alias=True)),
            "localOnly": True,
        },
    )


def _artifact_ref(arguments: Mapping[str, object], content_digest: str) -> str:
    value = arguments.get("artifactId") or arguments.get("artifactRef")
    if isinstance(value, str) and value.strip():
        return f"artifact:{hashlib.sha1(value.strip().encode('utf-8')).hexdigest()[:16]}"
    return f"artifact:{hashlib.sha1(content_digest.encode('utf-8')).hexdigest()[:16]}"


def _channel_ref(arguments: Mapping[str, object], context: ToolContext) -> ChannelRef:
    channel = arguments.get("channel")
    chat = arguments.get("chat")
    if not isinstance(channel, str) and isinstance(chat, Mapping):
        nested_channel = chat.get("channel")
        if isinstance(nested_channel, str):
            channel = nested_channel
    channel_id = str(channel or context.channel or "local").strip() or "local"
    return ChannelRef(type="web", channelId=f"channel:{_short_digest(channel_id)}")


def _mime_type(arguments: Mapping[str, object], filename: str) -> str:
    value = arguments.get("mimeType") or arguments.get("mime_type")
    if isinstance(value, str) and "/" in value:
        return value.strip().lower()[:120]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _request_id(tool_name: str, context: ToolContext, content_digest: str) -> str:
    base = context.tool_use_id or context.turn_id or context.session_id or content_digest
    return f"{tool_name}:{_short_digest(str(base) + ':' + content_digest)}"


def _session_key(context: ToolContext) -> str:
    base = context.session_key or context.session_id or context.turn_id or context.bot_id
    return f"session:{_short_digest(str(base))}"


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
