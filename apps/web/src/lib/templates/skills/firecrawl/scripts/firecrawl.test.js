"use strict";

const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const assert = require("node:assert/strict");

const scripts = [
  ["skill", readFileSync(join(__dirname, "firecrawl.sh"), "utf8")],
  ["lifecycle", readFileSync(join(__dirname, "../../../scripts/firecrawl.sh"), "utf8")],
];

test("uses CORE_AGENT_API_PROXY_URL as the platform proxy fallback", () => {
  for (const [name, script] of scripts) {
    assert.match(
      script,
      /:\s+"\$\{API_PROXY_URL:=\$CORE_AGENT_API_PROXY_URL\}"/,
      `${name} firecrawl wrapper should use CORE_AGENT_API_PROXY_URL fallback`
    );
  }
});

test("authenticates platform proxy calls with a bearer gateway token", () => {
  for (const [name, script] of scripts) {
    assert.match(
      script,
      /AUTH_HEADER="Authorization: Bearer \$GATEWAY_TOKEN"/,
      `${name} firecrawl wrapper should authenticate with gateway token`
    );
  }
});
