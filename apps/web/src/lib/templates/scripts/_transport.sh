#!/bin/sh

clawy_transport_helper_script() {
  if [ -n "$CORE_AGENT_RELIABLE_REQUEST_SCRIPT" ]; then
    printf '%s' "$CORE_AGENT_RELIABLE_REQUEST_SCRIPT"
    return 0
  fi
  if [ -f "/app/runtime/reliable-request.mjs" ]; then
    printf '%s' "/app/runtime/reliable-request.mjs"
    return 0
  fi
  printf '%s' "/app/runtime/reliable-request.mjs"
}

clawy_transport_node_bin() {
  if [ -n "$CORE_AGENT_RELIABLE_REQUEST_NODE" ]; then
    printf '%s' "$CORE_AGENT_RELIABLE_REQUEST_NODE"
    return 0
  fi
  printf '%s' "node"
}

clawy_transport_request() {
  if [ "${CORE_AGENT_TRANSPORT_RELIABILITY:-on}" = "off" ]; then
    "$(clawy_transport_node_bin)" - "$@" <<'NODE'
const { spawnSync } = require("node:child_process");

const rawArgs = process.argv.slice(2);
let method = "GET";
let url = "";
const headers = [];
let bodyFile = "";
const formFields = [];
const formFiles = [];

for (let i = 0; i < rawArgs.length; i += 1) {
  const arg = rawArgs[i];
  const next = rawArgs[i + 1] ?? "";
  if (arg === "--method") {
    method = next || method;
    i += 1;
  } else if (arg === "--url") {
    url = next;
    i += 1;
  } else if (arg === "--header") {
    headers.push(next);
    i += 1;
  } else if (arg === "--body-file") {
    bodyFile = next;
    i += 1;
  } else if (arg === "--form-field") {
    formFields.push(next);
    i += 1;
  } else if (arg === "--form-file") {
    formFiles.push(next);
    i += 1;
  }
}

function classify(statusCode, stderr) {
  const text = String(stderr || "").toLowerCase();
  if (statusCode >= 200 && statusCode < 300) return "success";
  if (statusCode === 429) return "rate_limited";
  if (statusCode === 401 || statusCode === 407) return "auth";
  if (statusCode === 403) return "permission";
  if (statusCode === 404) return "not_found";
  if ([400, 409, 410, 413, 415, 422].includes(statusCode)) return "input";
  if ([408, 425, 502, 503, 504].includes(statusCode) || statusCode >= 500) return "transient";
  if (text.includes("timed out") || text.includes("connection reset") || text.includes("econnreset")) return "transient";
  return "fatal";
}

if (!url) {
  process.stdout.write(JSON.stringify({
    ok: false,
    classification: "input",
    attemptCount: 1,
    message: "missing --url",
    retryExhausted: false
  }));
  process.exit(0);
}

const curlArgs = ["-sS", "-X", method];
for (const header of headers) {
  curlArgs.push("-H", header);
}
if (bodyFile) {
  curlArgs.push("--data-binary", `@${bodyFile}`);
}
for (const field of formFields) {
  curlArgs.push("-F", field);
}
for (const file of formFiles) {
  const equals = file.indexOf("=");
  if (equals > 0) {
    curlArgs.push("-F", `${file.slice(0, equals)}=@${file.slice(equals + 1)}`);
  } else {
    curlArgs.push("-F", `file=@${file}`);
  }
}
curlArgs.push("-w", "\n__CLAWY_STATUS__:%{http_code}", url);

const result = spawnSync("curl", curlArgs, { encoding: "utf8" });
if (result.error || result.status !== 0) {
  process.stdout.write(JSON.stringify({
    ok: false,
    classification: classify(0, result.stderr || result.error?.message),
    attemptCount: 1,
    message: result.stderr || result.error?.message || "curl execution failed",
    retryExhausted: false
  }));
  process.exit(0);
}

const stdout = result.stdout || "";
const marker = "\n__CLAWY_STATUS__:";
const markerIndex = stdout.lastIndexOf(marker);
const body = markerIndex >= 0 ? stdout.slice(0, markerIndex) : stdout;
const statusCode = markerIndex >= 0 ? Number.parseInt(stdout.slice(markerIndex + marker.length).trim(), 10) : 0;
const classification = classify(statusCode, result.stderr);
process.stdout.write(JSON.stringify({
  ok: statusCode >= 200 && statusCode < 300,
  statusCode,
  body,
  classification,
  attemptCount: 1,
  retryExhausted: false,
  ...(classification === "success" ? {} : { message: `HTTP ${statusCode || "unknown"} ${classification}` })
}));
NODE
    return 0
  fi
  _clawy_transport_script="$(clawy_transport_helper_script)"
  if [ ! -f "$_clawy_transport_script" ]; then
    printf '%s' '{"ok":false,"classification":"fatal","attemptCount":1,"message":"reliable request helper not found","retryExhausted":false}'
    return 0
  fi
  if ! "$(clawy_transport_node_bin)" "$_clawy_transport_script" "$@"; then
    printf '%s' '{"ok":false,"classification":"fatal","attemptCount":1,"message":"reliable request helper execution failed","retryExhausted":false}'
  fi
}

clawy_transport_is_ok() {
  printf '%s' "$1" | node -e '
    const fs = require("node:fs");
    try {
      const data = JSON.parse(fs.readFileSync(0, "utf8"));
      process.stdout.write(data.ok ? "true" : "false");
    } catch {
      process.stdout.write("false");
    }
  '
}

clawy_transport_body() {
  printf '%s' "$1" | node -e '
    const fs = require("node:fs");
    try {
      const data = JSON.parse(fs.readFileSync(0, "utf8"));
      process.stdout.write(typeof data.body === "string" ? data.body : "");
    } catch {
      process.stdout.write("");
    }
  '
}

clawy_transport_failure_json() {
  printf '%s' "$1" | node -e '
    const fs = require("node:fs");
    let data;
    try {
      data = JSON.parse(fs.readFileSync(0, "utf8"));
    } catch {
      data = {
        classification: "fatal",
        attemptCount: 1,
        retryExhausted: false,
        message: "reliable request helper returned invalid JSON"
      };
    }
    process.stdout.write(JSON.stringify({
      ok: false,
      error: "transport_request_failed",
      classification: data.classification ?? "fatal",
      attemptCount: data.attemptCount ?? 1,
      statusCode: data.statusCode,
      retryExhausted: Boolean(data.retryExhausted),
      message: data.message ?? "transport request failed"
    }));
  '
}
