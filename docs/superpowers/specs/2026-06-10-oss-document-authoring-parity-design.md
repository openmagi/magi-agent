# OSS Document Authoring Parity Design

## Problem

The hosted/legacy TypeScript runtime exposes first-party document authoring through
`DocumentWrite` and bundled `document-writer`/`hwpx` skills. That contract covers
Markdown, plain text, HTML, PDF, DOCX, and HWPX document creation, with richer
DOCX/HWPX authoring paths and delivery-ready artifact metadata.

The OSS Magi Agent repo currently advertises part of that surface in
`openmagi.documents`, but the implementation is incomplete:

- `DocumentWrite(format="docx")` writes a real DOCX through a local
  `python-docx` backend.
- Other formats fall through to raw text writes.
- PDF, HTML, and HWPX are not first-class format outputs.
- The bundled first-party `document-writer` and `hwpx` skills are absent.
- Catalog metadata already claims `docx`, `pdf`, and `html`, creating drift
  between projected capability and runtime behavior.

## Goals

- Restore user-facing parity with the hosted `DocumentWrite` contract for
  `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`.
- Add first-party bundled skills for document writing and HWPX workflows.
- Keep the implementation Python-native and scoped to the OSS runtime.
- Avoid new required dependencies.
- Preserve existing import boundaries: optional document dependencies must remain
  lazy and missing optional dependencies must return blocked tool results.
- Preserve workspace safety: every generated file must stay under
  `ToolContext.workspaceRoot`.
- Emit delivery-friendly artifact metadata for every successful format.
- Restore canonical Markdown export behavior for `renderer="canonical_markdown"`,
  `outputs`, `docxMode`, print QA metadata, and fixed-layout blocking semantics.
- Restore the HWPX runtime scripts/templates/guards needed for `base`, `gonmun`,
  `report`, and `minutes` templates.
- Provide an OSS-native agentic authoring boundary for DOCX/HWPX. When
  `MAGI_DOCUMENT_AGENTIC_MODEL` is configured, a LiteLLM-backed authoring worker
  rewrites the source into polished Markdown and materializes DOCX/HWPX through
  the same validated local writers; when it is not configured, the deterministic
  writer is the fallback and reports that mode in metadata.
- Add focused tests that fail before implementation and exercise parity end to
  end.

## Non-Goals

- No database, auth, billing, Kubernetes, or deploy changes.
- No hosted monorepo runtime implementation.
- No new PyPI dependency without explicit approval.
- No replacement of `FileDeliver` or channel delivery behavior.
- No attempt to reproduce the TypeScript code line-for-line. Parity is defined
  at the tool/skill contract and observable runtime behavior level.
- No silent success for unavailable external renderers or converters. Missing
  browser/PDF/agentic infrastructure must produce explicit metadata or blocked
  results, matching hosted behavior.

## Parity Contract

`DocumentWrite` must accept the hosted-style shape:

- `format`: one of `html`, `docx`, `hwpx`, `md`, `txt`, `pdf`.
- `path` or `filename`: workspace-relative output path.
- content source fields: `content`, `text`, `markdown`, or `source`.
- `source` may be a string, a markdown/text mapping with `kind` or `type` plus
  `content`, `markdown`, `text`, or `path`, or a structured mapping with
  `blocks` or `blocksFile`.
- `outputs` may request canonical `html`, `pdf`, and `docx` outputs; if absent,
  write one output from `format` or filename suffix.
- `title`, `locale`, `renderer`, `mode`, `preset`, `page`, `template`, and
  `docxMode` are accepted for compatibility. Unsupported advanced options must
  fail clearly only when they are essential to the requested output.
- `template` accepts `base`, `gonmun`, `report`, and `minutes`.
- `previewKind` matches hosted values: `inline-html` for HTML,
  `inline-markdown` for Markdown, and `download-only` for everything else.

Successful outputs return a stable projection including:

- `path` and `pathRef`
- `format`
- `mimeType`
- `previewKind`
- `contentDigest`
- `byteCount`
- `artifactRef` and `artifactRefs`
- `localOnly: true`

Blocked results use explicit error codes such as `content_required`,
`document_dependency_not_installed`, `pdf_converter_unavailable`,
`document_pdf_conversion_failed`, `hwpx_runtime_unavailable`,
`hwpx_validation_failed`, or the existing workspace path policy errors.

## Architecture

Add a small document authoring package under `magi_agent/tools/document_write/`
and leave `magi_agent/tools/document_write_tools.py` as the public compatibility
module for existing imports.

Planned modules:

- `model.py`: format enums, source normalization, output requests, artifact
  metadata helpers, MIME/preview mapping.
- `markdown.py`: pragmatic Markdown parsing into an internal block model and
  safe HTML rendering.
- `text.py`: Markdown/plain text file writers.
- `docx.py`: existing deterministic DOCX renderer, moved behind the shared
  output contract while preserving current `docx_write()` behavior.
- `pdf.py`: DOCX-to-PDF conversion through `libreoffice`/`soffice`, with strict
  `%PDF-` validation and clear blocked results when no converter is present.
- `canonical.py`: canonical Markdown parser/exporter for hosted-compatible
  `renderer="canonical_markdown"` outputs, QA JSON, editable DOCX, and explicit
  blocked results for browser-required PDF or fixed-layout DOCX when no renderer
  is configured.
- `hwpx.py`: deterministic HWPX package writer and validator backed by bundled
  runtime templates/scripts, including `validate.py`, `content_guard.py`, and
  `page_guard.py` when applicable.
- `agentic.py`: model-backed DOCX/HWPX authoring boundary enabled by
  `MAGI_DOCUMENT_AGENTIC_MODEL`, plus deterministic fallback metadata when no
  model client is configured or the authoring attempt fails.
- `orchestrator.py`: `document_write()` multi-format dispatch, filename/format
  inference, redaction, and result aggregation.

The native plugin entrypoint remains
`magi_agent.plugins.native.documents:document_write`; it delegates to the new
orchestrator lazily.

## Format Behavior

### Markdown and Plain Text

`md` writes redacted Markdown source unchanged. `txt` writes plain extracted text
from the same source. Both paths use the same workspace and redaction policy as
the existing Markdown fallback.

### HTML

HTML is rendered from the canonical block model, escapes raw user content, and
adds minimal print-friendly CSS. Raw HTML in source is treated as text, not
trusted markup.

### Canonical Markdown Renderer

When `renderer="canonical_markdown"` is requested, the writer parses source as
canonical Markdown and can emit any combination of `html`, `pdf`, and `docx`.
Editable DOCX is generated with the existing optional DOCX dependency. PDF and
fixed-layout DOCX require a browser/PDF render provider because hosted rendered
HTML to paginated PDF/screenshots for those modes. If the provider is absent,
the tool returns a blocked result such as
`canonical_markdown_renderer_unavailable`. Successful canonical exports include
`documentWriteMode`, `canonicalMarkdownQa`, and all output artifacts.

### DOCX

DOCX continues to use `python-docx` from the optional `files` extra. The existing
coverage evidence behavior remains intact. The output projection is expanded to
match the shared parity metadata. If `MAGI_DOCUMENT_AGENTIC_MODEL` is
configured, DOCX first attempts model-backed authoring and falls back to
deterministic rendering on authoring failure, recording `documentWriteMode` and
agentic error metadata.

### PDF

The default PDF path mirrors hosted behavior: render DOCX first, then convert it
with `libreoffice` or `soffice` in headless mode. If no converter is available,
the tool returns `pdf_converter_unavailable` instead of pretending success.

The implementation does not add a direct PDF library dependency. Canonical
Markdown PDF uses the browser-render provider when configured; default PDF uses
DOCX-to-PDF conversion.

### HWPX

HWPX is written as a valid zipped HWPX package using bundled Python runtime
assets. The writer emits the required package structure, escapes XML content, and
validates that the resulting ZIP contains the expected manifest/header/section
files. Template presets `base`, `gonmun`, `report`, and `minutes` must route
through bundled runtime templates. Reference-template editing follows the hosted
shape at the schema and guard boundary: template references are safely resolved;
configured agentic HWPX output must pass bundled validation/content guards and
`page_guard.py`; without a configured authoring path it returns
`hwpx_reference_template_requires_agentic_authoring`.

## Skills

Add bundled first-party skills:

- `magi_agent/skills/bundled/document-writer/SKILL.md`
- `magi_agent/skills/bundled/hwpx/SKILL.md`

The skills should direct agents to:

- draft the source as Markdown first when possible;
- use `DocumentWrite` for `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`;
- use `FileDeliver` after generation when the user needs the artifact delivered;
- prefer `DocumentWrite(format="hwpx")` for Korean HWPX documents;
- avoid ad hoc shell scripts unless the first-party tool is blocked.

Package data globs must include these bundled skills and HWPX runtime assets.

## Testing Strategy

Use TDD. Add failing tests before implementation for:

- format dispatch for `md`, `txt`, `html`, `pdf`, `docx`, and `hwpx`;
- hosted-compatible `source` shapes and `outputs`;
- artifact metadata parity for all successful outputs;
- schema/projection parity for `format`, `renderer`, `outputs`, `docxMode`,
  `preset`, `page`, `locale`, `template`, and hosted source shapes;
- canonical renderer success for HTML/editable DOCX and deterministic blocked
  behavior for browser-required PDF/fixed-layout outputs without a renderer;
- HTML escaping and print stylesheet presence;
- PDF converter blocked, failure, invalid-output, timeout, and success paths
  using monkeypatched deterministic converter behavior;
- valid HWPX ZIP structure, templates, bundled validator/content guard behavior,
  and validation failure handling;
- agentic writer selection/fallback metadata using fake in-process model/writer
  adapters rather than live model calls;
- bundled skill discovery and package data globs;
- native catalog/tool projection allowed format metadata.

Run focused tests first, then broader plugin/toolhost tests:

- `tests/tools/test_document_write_tools.py`
- new document parity tests
- bundled skill/package tests
- `tests/test_native_plugin_catalog.py`
- `tests/test_plugin_tool_projection.py`

## Rollout

This is a runtime/tooling change in the OSS repo only. There is no production
deploy in this task. The PR should describe the observable parity restored,
remaining limitations, and verification commands.
