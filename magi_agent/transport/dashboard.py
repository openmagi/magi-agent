from __future__ import annotations

import json
from html import escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _dashboard_html(runtime: OpenMagiRuntime) -> str:
    bootstrap = {
        "botId": runtime.config.bot_id,
        "model": runtime.config.model,
        "runtime": "magi-agent",
        "runtimeEngine": runtime.config.runtime_engine,
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
      --bg: #f5f6fa;
      --surface: #ffffff;
      --surface-2: #fbfcff;
      --surface-3: #eef1f6;
      --ink: #222736;
      --muted: #6d7484;
      --soft: #9aa3b5;
      --line: #dde2eb;
      --line-strong: #cfd6e2;
      --accent: #7047d8;
      --accent-soft: #efe8ff;
      --green: #2fbf7b;
      --red: #d9495f;
      --amber: #b7791f;
      --shadow: 0 14px 36px rgba(28, 34, 48, 0.08);
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    button, input, textarea, select {{ font: inherit; letter-spacing: 0; }}
    button {{ cursor: pointer; }}
    .app {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 372px;
      min-height: 100vh;
    }}
    .sidebar {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
      background: var(--surface);
      border-right: 1px solid var(--line);
    }}
    .brand {{
      padding: 22px 18px 18px;
      border-bottom: 1px solid var(--line);
    }}
    .brand h1 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.1;
    }}
    .brand-meta {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .dot {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--soft);
      flex: none;
    }}
    .dot.ready {{ background: var(--green); }}
    .dot.error {{ background: var(--red); }}
    .channel-list {{
      overflow: auto;
      padding: 18px 12px;
    }}
    .section-label {{
      margin: 0 0 10px 8px;
      color: var(--soft);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .channel {{
      width: 100%;
      display: grid;
      grid-template-columns: 22px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 40px;
      margin: 2px 0;
      padding: 0 10px;
      border: 0;
      border-radius: var(--radius);
      background: transparent;
      color: var(--muted);
      text-align: left;
    }}
    .channel:hover {{
      background: var(--surface-3);
      color: var(--ink);
    }}
    .channel.active {{
      color: var(--ink);
      background: var(--accent-soft);
      font-weight: 700;
    }}
    .channel .badge {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      opacity: 0;
    }}
    .channel.active .badge {{ opacity: 1; }}
    .sidebar-footer {{
      display: grid;
      gap: 8px;
      padding: 14px 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }}
    .main {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-width: 0;
      min-height: 100vh;
      background: var(--bg);
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 66px;
      padding: 0 24px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }}
    .topbar-title {{
      min-width: 0;
    }}
    .topbar-title h2 {{
      margin: 0;
      font-size: 17px;
    }}
    .topbar-title p {{
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .topbar-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .icon-button {{
      width: 34px;
      height: 34px;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--muted);
    }}
    .icon-button:hover {{ color: var(--ink); border-color: var(--line-strong); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface-2);
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }}
    .messages {{
      overflow: auto;
      padding: 24px min(7vw, 78px);
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    .empty-state {{
      max-width: 720px;
      width: 100%;
      margin: 12px auto 0;
      color: var(--muted);
      text-align: left;
    }}
    .empty-state h3 {{
      margin: 0 0 8px;
      color: var(--ink);
      font-size: 22px;
      line-height: 1.2;
    }}
    .empty-state p {{
      margin: 0;
      line-height: 1.5;
    }}
    .welcome-message {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: 0 5px 22px rgba(31, 38, 52, 0.05);
      padding: 18px;
    }}
    .trace-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .trace-item {{
      min-height: 62px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface-2);
      padding: 10px;
    }}
    .trace-item strong {{
      display: block;
      color: var(--ink);
      font-size: 13px;
      margin-bottom: 4px;
    }}
    .trace-item span {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}
    .starter-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 22px;
    }}
    .starter {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 12px;
      color: var(--ink);
      text-align: left;
      line-height: 1.35;
    }}
    .starter:hover {{
      border-color: var(--line-strong);
      box-shadow: 0 5px 16px rgba(31, 38, 52, 0.06);
    }}
    .message {{
      max-width: 880px;
      white-space: pre-wrap;
      line-height: 1.58;
      word-break: break-word;
    }}
    .message.user {{
      align-self: flex-end;
      padding: 12px 14px;
      border-radius: var(--radius);
      background: var(--accent-soft);
      border: 1px solid #ddceff;
    }}
    .message.assistant {{
      align-self: flex-start;
      padding: 16px 18px;
      border-radius: var(--radius);
      background: var(--surface);
      border: 1px solid var(--line);
      box-shadow: 0 4px 18px rgba(31, 38, 52, 0.05);
    }}
    .message.system {{
      align-self: stretch;
      max-width: 880px;
      color: var(--muted);
      background: transparent;
      border: 0;
      padding: 0;
      box-shadow: none;
    }}
    .message.error {{
      border-color: #f2bfca;
      background: #fff4f6;
      color: #8a2638;
    }}
    .composer-wrap {{
      padding: 16px min(7vw, 78px) 22px;
      background: linear-gradient(180deg, rgba(246,247,251,0), var(--bg) 18%);
    }}
    .composer {{
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }}
    .composer-strip {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 44px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
    }}
    .mode {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    textarea {{
      width: 100%;
      min-height: 104px;
      max-height: 260px;
      resize: vertical;
      border: 0;
      outline: 0;
      padding: 16px 18px;
      color: var(--ink);
      background: var(--surface);
      line-height: 1.5;
    }}
    .composer-actions {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
    }}
    .token-field {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }}
    .token-field label {{
      color: var(--soft);
      font-size: 12px;
      white-space: nowrap;
    }}
    .token-field input {{
      width: 100%;
      height: 34px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 0 10px;
      color: var(--ink);
      background: var(--surface-2);
    }}
    .send {{
      min-width: 86px;
      height: 38px;
      border: 0;
      border-radius: var(--radius);
      background: var(--accent);
      color: white;
      font-weight: 700;
    }}
    .send:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
    }}
    .inspector {{
      display: grid;
      grid-template-rows: auto auto 1fr;
      min-height: 100vh;
      background: var(--surface-2);
      border-left: 1px solid var(--line);
    }}
    .inspector-head {{
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }}
    .inspector-head h2 {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .tabs {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0;
      margin: 12px 14px;
      padding: 3px;
      border-radius: var(--radius);
      background: var(--surface-3);
    }}
    .tab {{
      height: 34px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--soft);
      font-weight: 700;
      font-size: 13px;
    }}
    .tab.active {{
      background: var(--surface);
      color: var(--ink);
      box-shadow: 0 1px 4px rgba(23, 30, 43, 0.1);
    }}
    .panel {{
      overflow: auto;
      padding: 0 14px 18px;
    }}
    .timeline {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .event {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 10px 11px;
      font-size: 12px;
      color: var(--muted);
    }}
    .event.pending {{
      border-color: #d8ccff;
      background: #fbf9ff;
    }}
    .event strong {{
      display: block;
      margin-bottom: 4px;
      color: var(--ink);
      font-size: 13px;
    }}
    .event code {{
      display: block;
      overflow-wrap: anywhere;
      margin-top: 6px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 11px;
      line-height: 1.45;
    }}
    .kv {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
    .kv-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .kv-row strong {{ color: var(--ink); font-weight: 600; }}
    .knowledge-list {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
    .knowledge-item {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-height: 34px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--muted);
      font-size: 13px;
    }}
    .status-band {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}
    .status-tile {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 10px;
      min-height: 58px;
    }}
    .status-tile span {{
      display: block;
      color: var(--soft);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .status-tile strong {{
      display: block;
      margin-top: 5px;
      color: var(--ink);
      font-size: 13px;
    }}
    .hidden {{ display: none !important; }}
    @media (max-width: 1120px) {{
      .app {{ grid-template-columns: 220px minmax(0, 1fr); }}
      .inspector {{ grid-column: 1 / -1; min-height: 420px; border-left: 0; border-top: 1px solid var(--line); }}
    }}
    @media (max-width: 760px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ min-height: auto; }}
      .channel-list {{ display: none; }}
      .messages, .composer-wrap {{ padding-left: 16px; padding-right: 16px; }}
      .topbar {{ padding: 0 16px; }}
      .topbar-actions .pill:not(#chat-route-pill) {{ display: none; }}
      .starter-grid {{ grid-template-columns: 1fr; }}
      .trace-grid {{ grid-template-columns: 1fr; }}
      .composer-actions {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <script type="application/json" id="runtime-bootstrap">{bootstrap_json}</script>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>Open Magi Agent</h1>
        <div class="brand-meta"><span class="dot" id="runtime-dot"></span><span id="runtime-label">Checking runtime</span></div>
      </div>
      <nav class="channel-list" aria-label="Local channels">
        <p class="section-label">General</p>
        <button class="channel active" type="button"><span>#</span><span>general</span><span class="badge"></span></button>
        <button class="channel" type="button"><span>#</span><span>research</span><span class="badge"></span></button>
        <button class="channel" type="button"><span>#</span><span>coding</span><span class="badge"></span></button>
        <button class="channel" type="button"><span>#</span><span>automation</span><span class="badge"></span></button>
        <p class="section-label" style="margin-top:18px">Runtime</p>
        <button class="channel" type="button"><span>*</span><span>Memory</span><span></span></button>
        <button class="channel" type="button"><span>*</span><span>Tools</span><span></span></button>
        <button class="channel" type="button"><span>*</span><span>Evidence</span><span></span></button>
      </nav>
      <div class="sidebar-footer">
        <span id="footer-runtime">magi-agent</span>
        <span id="footer-version"></span>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-title">
          <h2># general</h2>
          <p id="route-summary">Local Magi Agent workspace</p>
        </div>
        <div class="topbar-actions">
          <button class="icon-button" type="button" title="Refresh runtime health" id="refresh-health">&#8635;</button>
          <span class="pill"><span class="dot ready"></span><span>ADK runtime</span></span>
          <span class="pill" id="chat-route-pill">chat route unknown</span>
        </div>
      </header>

      <section class="messages" id="messages" aria-live="polite">
        <div class="empty-state" id="empty-state">
          <div class="welcome-message">
            <h3>Open Magi Agent is ready.</h3>
            <p>Run research, coding, document review, planning, and automation from this local workspace.</p>
            <div class="trace-grid" aria-label="Runtime surfaces">
              <div class="trace-item"><strong>Work stream</strong><span>Tool progress, runtime events, receipts, and transport state.</span></div>
              <div class="trace-item"><strong>Knowledge</strong><span>Workspace files, memory receipts, and evidence records.</span></div>
              <div class="trace-item"><strong>Policy</strong><span>Harness gates keep high-authority work explicit.</span></div>
            </div>
          </div>
          <div class="starter-grid">
            <button class="starter" type="button" data-prompt="Inspect this repository and summarize the runnable local surfaces.">Inspect this repository</button>
            <button class="starter" type="button" data-prompt="Draft a short research plan and list the evidence gates you would use.">Plan a research task</button>
            <button class="starter" type="button" data-prompt="Create a coding checklist for fixing a failing test, including rollback evidence.">Plan a coding fix</button>
            <button class="starter" type="button" data-prompt="Show the runtime health, active tools, and current policy boundaries.">Check runtime health</button>
          </div>
        </div>
      </section>

      <section class="composer-wrap">
        <form class="composer" id="chat-form">
          <div class="composer-strip">
            <span class="mode"><span class="dot ready"></span>Live run</span>
            <span class="mode">Streams ADK events when the runtime emits them</span>
          </div>
          <textarea id="prompt" placeholder="Ask the local agent to inspect, write, research, or plan..."></textarea>
          <div class="composer-actions">
            <div class="token-field">
              <label for="gateway-token">Gateway token</label>
              <input id="gateway-token" type="password" autocomplete="current-password" placeholder="local-dev-token unless you set GATEWAY_TOKEN">
            </div>
            <button class="send" id="send-button" type="submit">Send</button>
          </div>
        </form>
      </section>
    </main>

    <aside class="inspector">
      <div class="inspector-head">
        <h2>Work Stream</h2>
        <div class="brand-meta">Runtime events, tool progress, evidence, and SSE state.</div>
        <div class="status-band">
          <div class="status-tile"><span>Runtime</span><strong id="tile-runtime">magi-agent</strong></div>
          <div class="status-tile"><span>State</span><strong id="tile-state">checking</strong></div>
        </div>
        <div class="kv" id="runtime-kv"></div>
      </div>
      <div class="tabs" role="tablist">
        <button class="tab active" type="button" data-panel="work">Work</button>
        <button class="tab" type="button" data-panel="knowledge">Knowledge</button>
        <button class="tab" type="button" data-panel="settings">Settings</button>
      </div>
      <div class="panel">
        <div id="panel-work" class="timeline">
          <div class="event pending"><strong>Runtime check</strong><code>Waiting for /healthz</code></div>
        </div>
        <div id="panel-knowledge" class="hidden">
          <p class="brand-meta">Local knowledge and artifacts are exposed by runtime contracts when enabled.</p>
          <div class="knowledge-list">
            <div class="knowledge-item"><span>[]</span><span>Workspace files</span></div>
            <div class="knowledge-item"><span>[]</span><span>Memory receipts</span></div>
            <div class="knowledge-item"><span>[]</span><span>Evidence ledger</span></div>
          </div>
        </div>
        <div id="panel-settings" class="hidden">
          <div class="kv">
            <div class="kv-row"><span>Runtime</span><strong id="settings-runtime">magi-agent</strong></div>
            <div class="kv-row"><span>Model</span><strong id="settings-model">local-dev</strong></div>
            <div class="kv-row"><span>Bot</span><strong id="settings-bot">local-bot</strong></div>
            <div class="kv-row"><span>Engine</span><strong id="settings-engine">adk-python</strong></div>
          </div>
        </div>
      </div>
    </aside>
  </div>

  <script>
    const bootstrap = JSON.parse(document.getElementById("runtime-bootstrap").textContent);
    const messages = document.getElementById("messages");
    const emptyState = document.getElementById("empty-state");
    const workPanel = document.getElementById("panel-work");
    const form = document.getElementById("chat-form");
    const promptInput = document.getElementById("prompt");
    const tokenInput = document.getElementById("gateway-token");
    const sendButton = document.getElementById("send-button");
    const runtimeDot = document.getElementById("runtime-dot");
    const runtimeLabel = document.getElementById("runtime-label");
    const runtimeKv = document.getElementById("runtime-kv");
    const chatRoutePill = document.getElementById("chat-route-pill");
    const tileRuntime = document.getElementById("tile-runtime");
    const tileState = document.getElementById("tile-state");
    const tokenKey = "magi-agent:gateway-token";

    document.getElementById("footer-runtime").textContent = `${{bootstrap.runtime}} / ${{bootstrap.botId}}`;
    document.getElementById("footer-version").textContent = `version ${{bootstrap.version}}`;
    document.getElementById("settings-runtime").textContent = bootstrap.runtime;
    document.getElementById("settings-model").textContent = bootstrap.model;
    document.getElementById("settings-bot").textContent = bootstrap.botId;
    document.getElementById("settings-engine").textContent = bootstrap.runtimeEngine || "adk-python";
    tileRuntime.textContent = bootstrap.runtime;
    tokenInput.value = localStorage.getItem(tokenKey) || "";

    function escapeText(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }}[char]));
    }}

    function compactJson(value) {{
      try {{
        return JSON.stringify(value, null, 2);
      }} catch (error) {{
        return String(value);
      }}
    }}

    function addEvent(title, detail, tone) {{
      const node = document.createElement("div");
      node.className = tone === "pending" ? "event pending" : "event";
      const safeDetail = detail ? `<code>${{escapeText(detail)}}</code>` : "";
      node.innerHTML = `<strong>${{escapeText(title)}}</strong>${{safeDetail}}`;
      if (tone === "error") node.style.borderColor = "#f2bfca";
      if (tone === "ok") node.style.borderColor = "#b7e7cf";
      workPanel.appendChild(node);
      workPanel.scrollTop = workPanel.scrollHeight;
      return node;
    }}

    function addMessage(role, text, tone) {{
      emptyState.classList.add("hidden");
      const node = document.createElement("div");
      node.className = `message ${{role}}${{tone ? " " + tone : ""}}`;
      node.textContent = text || "";
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
      return node;
    }}

    function setHealth(ok, label) {{
      runtimeDot.className = `dot ${{ok ? "ready" : "error"}}`;
      runtimeLabel.textContent = label;
    }}

    function renderHealth(body, ok) {{
      runtimeKv.innerHTML = "";
      const rows = [
        ["Runtime", bootstrap.runtime],
        ["Engine", bootstrap.runtimeEngine || "adk-python"],
        ["Model", bootstrap.model],
        ["Build", bootstrap.version],
      ];
      const gateStatus = body && (body.status || body.readinessStatus || body.runtimeStatus);
      if (gateStatus) rows.push(["Status", gateStatus]);
      for (const [label, value] of rows) {{
        const row = document.createElement("div");
        row.className = "kv-row";
        row.innerHTML = `<span>${{escapeText(label)}}</span><strong>${{escapeText(value)}}</strong>`;
        runtimeKv.appendChild(row);
      }}
      chatRoutePill.textContent = ok ? "runtime ready" : "runtime blocked";
      tileState.textContent = ok ? "ready" : "blocked";
    }}

    async function checkHealth() {{
      try {{
        const response = await fetch("/healthz");
        const body = await response.json();
        setHealth(response.ok, response.ok ? "active" : "blocked");
        renderHealth(body, response.ok);
        addEvent("Runtime health", compactJson({{ ok: response.ok, status: body.status || "ready" }}), response.ok ? "ok" : "error");
      }} catch (error) {{
        setHealth(false, "unavailable");
        chatRoutePill.textContent = "runtime unavailable";
        tileState.textContent = "unavailable";
        addEvent("Runtime unavailable", "Could not reach /healthz", "error");
      }}
    }}

    function appendDelta(target, payload) {{
      const choices = payload && payload.choices;
      const delta = choices && choices[0] && choices[0].delta;
      const content = delta && delta.content;
      if (content) target.textContent += content;
    }}

    function summarizeAgentEvent(payload) {{
      const type = payload && (payload.type || payload.eventType || payload.status || "agent");
      const titleByType = {{
        turn_start: "Turn started",
        turn_end: "Turn ended",
        tool_start: "Tool started",
        tool_end: "Tool completed",
        tool_error: "Tool failed",
        source_inspected: "Source inspected",
        rule_check: "Rule check",
        llm_progress: "Model progress",
        patch_preview: "Patch preview",
        coding_final_projection: "Coding projection",
        research_final_projection: "Research projection",
        runtime_trace: "Runtime trace",
        error: "Runtime error",
      }};
      return titleByType[type] || String(type).replace(/_/g, " ");
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
        addEvent("Completed", "SSE stream finished", "ok");
        return true;
      }}
      try {{
        const parsed = JSON.parse(rawData);
        appendDelta(target, parsed);
        if (eventName === "agent") {{
          addEvent(summarizeAgentEvent(parsed), compactJson(parsed));
        }} else if (eventName !== "message" || parsed.type || parsed.event || parsed.status) {{
          addEvent(`event: ${{eventName}}`, compactJson(parsed));
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
      addEvent("Request", "POST /v1/chat/completions", "pending");
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
        assistant.className = "message assistant error";
        assistant.textContent = text || `Request failed: ${{response.status}}`;
        addEvent("Request failed", assistant.textContent, "error");
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
      if (!assistant.textContent.trim()) {{
        assistant.textContent = "The runtime completed without user-visible text. Check the work stream for events and receipts.";
      }}
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

    promptInput.addEventListener("keydown", (event) => {{
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {{
        form.requestSubmit();
      }}
    }});

    for (const starter of document.querySelectorAll(".starter")) {{
      starter.addEventListener("click", () => {{
        promptInput.value = starter.dataset.prompt || "";
        promptInput.focus();
      }});
    }}

    for (const tab of document.querySelectorAll(".tab")) {{
      tab.addEventListener("click", () => {{
        for (const current of document.querySelectorAll(".tab")) current.classList.remove("active");
        tab.classList.add("active");
        for (const id of ["work", "knowledge", "settings"]) {{
          document.getElementById(`panel-${{id}}`).classList.toggle("hidden", id !== tab.dataset.panel);
        }}
      }});
    }}

    document.getElementById("refresh-health").addEventListener("click", () => {{
      checkHealth();
    }});

    checkHealth();
  </script>
</body>
</html>"""


def register_dashboard_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/", response_class=RedirectResponse)
    def root_dashboard() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=307)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_dashboard_html(runtime))

    @app.get("/dashboard/{path:path}", response_class=HTMLResponse)
    def dashboard_deep_link(path: str) -> HTMLResponse:
        return HTMLResponse(_dashboard_html(runtime))
