from __future__ import annotations

import json
from html import escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _dashboard_html(runtime: OpenMagiRuntime) -> str:
    bootstrap = {
        "botId": runtime.config.bot_id,
        "model": runtime.config.model,
        "runtime": "magi-agent",
        "version": runtime.config.build.version,
    }
    bootstrap_json = escape(json.dumps(bootstrap, separators=(",", ":")), quote=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Magi Agent Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #1f2430;
      --muted: #687084;
      --line: #dde2eb;
      --accent: #6d47d9;
      --accent-2: #0f8a6a;
      --danger: #be3455;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      min-height: 100vh;
    }}
    .workspace {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
      border-right: 1px solid var(--line);
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--muted);
      background: #fbfcfe;
      font-size: 13px;
    }}
    .dot {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--muted);
    }}
    .dot.ready {{ background: var(--accent-2); }}
    .dot.error {{ background: var(--danger); }}
    #messages {{
      overflow: auto;
      padding: 28px 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .message {{
      max-width: 900px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      white-space: pre-wrap;
      line-height: 1.5;
    }}
    .message.user {{
      align-self: flex-end;
      border-color: #d8cef7;
      background: #f5f1ff;
    }}
    .message.assistant {{
      align-self: flex-start;
    }}
    form {{
      display: grid;
      gap: 10px;
      padding: 18px 24px 22px;
      background: var(--panel);
      border-top: 1px solid var(--line);
    }}
    textarea, input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
      font: inherit;
      letter-spacing: 0;
    }}
    textarea {{
      min-height: 96px;
      resize: vertical;
      padding: 12px;
      line-height: 1.45;
    }}
    input {{
      height: 38px;
      padding: 0 10px;
    }}
    .controls {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }}
    button {{
      height: 38px;
      min-width: 96px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font: inherit;
      cursor: pointer;
    }}
    button:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
    }}
    aside {{
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 100vh;
      background: #fbfcfe;
    }}
    .side-head {{
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }}
    .side-head h2 {{
      margin: 0 0 8px;
      font-size: 14px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .meta {{
      display: grid;
      gap: 6px;
      font-size: 13px;
      color: var(--muted);
    }}
    #events {{
      overflow: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .event {{
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      font-size: 12px;
      color: var(--muted);
      white-space: pre-wrap;
      word-break: break-word;
    }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ min-height: 360px; border-top: 1px solid var(--line); }}
      .workspace {{ min-height: 70vh; border-right: 0; }}
    }}
  </style>
</head>
<body>
  <script type="application/json" id="runtime-bootstrap">{bootstrap_json}</script>
  <main>
    <section class="workspace">
      <header>
        <div>
          <h1>Open Magi Agent</h1>
          <div class="meta" id="runtime-meta"></div>
        </div>
        <div class="status"><span class="dot" id="health-dot"></span><span id="health-text">Checking runtime</span></div>
      </header>
      <div id="messages" aria-live="polite"></div>
      <form id="chat-form">
        <div class="controls">
          <label>
            <span class="meta">Gateway token</span>
            <input id="gateway-token" type="password" autocomplete="current-password" placeholder="Paste local token">
          </label>
          <button id="send-button" type="submit">Send</button>
        </div>
        <textarea id="prompt" placeholder="Ask the local agent to inspect, write, research, or plan..."></textarea>
      </form>
    </section>
    <aside>
      <div class="side-head">
        <h2>Work Stream</h2>
        <div class="meta">Public runtime events, including event: agent frames, and SSE transport state.</div>
      </div>
      <div id="events" aria-live="polite"></div>
    </aside>
  </main>
  <script>
    const bootstrap = JSON.parse(document.getElementById("runtime-bootstrap").textContent);
    const messages = document.getElementById("messages");
    const events = document.getElementById("events");
    const form = document.getElementById("chat-form");
    const promptInput = document.getElementById("prompt");
    const tokenInput = document.getElementById("gateway-token");
    const sendButton = document.getElementById("send-button");
    const healthDot = document.getElementById("health-dot");
    const healthText = document.getElementById("health-text");
    const runtimeMeta = document.getElementById("runtime-meta");
    const tokenKey = "magi-agent:gateway-token";

    runtimeMeta.textContent = `${{bootstrap.runtime}} / ${{bootstrap.botId}} / ${{bootstrap.model}}`;
    tokenInput.value = localStorage.getItem(tokenKey) || "";

    function addMessage(role, text) {{
      const node = document.createElement("div");
      node.className = `message ${{role}}`;
      node.textContent = text;
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
      return node;
    }}

    function addEvent(label, value) {{
      const node = document.createElement("div");
      node.className = "event";
      node.textContent = value ? `${{label}}\\n${{value}}` : label;
      events.appendChild(node);
      events.scrollTop = events.scrollHeight;
    }}

    async function checkHealth() {{
      try {{
        const response = await fetch("/healthz");
        const body = await response.json();
        healthDot.className = `dot ${{response.ok ? "ready" : "error"}}`;
        healthText.textContent = response.ok ? "Runtime ready" : `Runtime blocked: ${{body.status || "check healthz"}}`;
      }} catch (error) {{
        healthDot.className = "dot error";
        healthText.textContent = "Runtime unavailable";
      }}
    }}

    function appendDelta(target, payload) {{
      const choices = payload && payload.choices;
      const delta = choices && choices[0] && choices[0].delta;
      const content = delta && delta.content;
      if (content) target.textContent += content;
    }}

    function renderSseBlock(target, block) {{
      let eventName = "message";
      const data = [];
      for (const line of block.split(/\\r?\\n/)) {{
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }}
      const rawData = data.join("\\n");
      if (!rawData) return false;
      if (rawData === "[DONE]") {{
        addEvent("done", "SSE stream completed");
        return true;
      }}
      try {{
        const parsed = JSON.parse(rawData);
        appendDelta(target, parsed);
        if (eventName !== "message" || parsed.type || parsed.event || parsed.status) {{
          addEvent(`event: ${{eventName}}`, JSON.stringify(parsed, null, 2));
        }}
      }} catch (error) {{
        addEvent(`event: ${{eventName}}`, rawData);
      }}
      return false;
    }}

    async function sendPrompt(prompt) {{
      const token = tokenInput.value.trim();
      localStorage.setItem(tokenKey, token);
      const assistant = addMessage("assistant", "");
      addEvent("request", "POST /v1/chat/completions");
      const response = await fetch("/v1/chat/completions", {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "Authorization": `Bearer ${{token}}`,
        }},
        body: JSON.stringify({{
          model: bootstrap.model,
          messages: [{{ role: "user", content: prompt }}],
          stream: true,
        }}),
      }});
      if (!response.ok) {{
        const text = await response.text();
        assistant.textContent = text || `Request failed: ${{response.status}}`;
        addEvent("error", assistant.textContent);
        return;
      }}
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {{
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, {{ stream: true }});
        const parts = buffer.split(/\\n\\n/);
        buffer = parts.pop() || "";
        for (const part of parts) renderSseBlock(assistant, part);
      }}
      if (buffer.trim()) renderSseBlock(assistant, buffer);
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const prompt = promptInput.value.trim();
      if (!prompt) return;
      addMessage("user", prompt);
      promptInput.value = "";
      sendButton.disabled = true;
      try {{
        await sendPrompt(prompt);
      }} finally {{
        sendButton.disabled = false;
        promptInput.focus();
      }}
    }});

    checkHealth();
  </script>
</body>
</html>"""


def register_dashboard_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_dashboard_html(runtime))
