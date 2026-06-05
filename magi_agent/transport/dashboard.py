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
        "gatewayToken": "local-dev-token" if runtime.config.gateway_token == "local-dev-token" else "",
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
      --surface: #ffffff;
      --surface-2: #fbfcff;
      --surface-3: #eef1f7;
      --surface-4: #f4f0ff;
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
      --shadow-soft: 0 5px 18px rgba(31, 38, 52, 0.05);
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
      padding: 22px 18px 16px;
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
    .brand-subtitle {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
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
      transition: background 150ms ease, color 150ms ease, border-color 150ms ease;
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
    .footer-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
    }}
    .footer-row strong {{
      color: var(--ink);
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
      background:
        linear-gradient(180deg, rgba(255,255,255,0.6), rgba(247,248,251,0) 230px),
        var(--bg);
    }}
    .run-summary {{
      max-width: 980px;
      width: 100%;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .summary-card {{
      min-height: 78px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: var(--shadow-soft);
      padding: 12px;
    }}
    .summary-card span {{
      display: block;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .summary-card strong {{
      display: block;
      margin-top: 7px;
      color: var(--ink);
      font-size: 14px;
      line-height: 1.25;
    }}
    .summary-card small {{
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}
    .empty-state {{
      max-width: 980px;
      width: 100%;
      margin: 0 auto;
      color: var(--muted);
      text-align: left;
    }}
    .empty-state h3 {{
      margin: 0 0 8px;
      color: var(--ink);
      font-size: 18px;
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
      box-shadow: var(--shadow-soft);
      padding: 18px;
    }}
    .thread-list {{
      max-width: 980px;
      width: 100%;
      margin: 0 auto;
      display: grid;
      gap: 12px;
    }}
    .thread-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 32px;
      color: var(--muted);
      font-size: 13px;
    }}
    .thread-meta span {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .thread-meta strong {{
      color: var(--ink);
      font-weight: 700;
    }}
    .run-state {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .run-state-card {{
      min-height: 64px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface-2);
      padding: 10px;
    }}
    .run-state-card span {{
      display: block;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .run-state-card strong {{
      display: block;
      margin-top: 6px;
      color: var(--ink);
      font-size: 13px;
    }}
    .surface-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .surface-card {{
      min-height: 82px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 12px;
    }}
    .surface-card strong {{
      display: block;
      color: var(--ink);
      font-size: 13px;
      margin-bottom: 5px;
    }}
    .surface-card span {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .quick-actions {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .health-rail {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .health-chip {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface-2);
      color: var(--muted);
      padding: 0 10px;
      font-size: 12px;
      font-weight: 700;
    }}
    .health-chip strong {{
      color: var(--ink);
      font-weight: 700;
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
      margin-top: 14px;
    }}
    .starter {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 12px;
      color: var(--ink);
      text-align: left;
      line-height: 1.35;
      min-height: 58px;
    }}
    .starter:hover {{
      border-color: var(--line-strong);
      box-shadow: 0 5px 16px rgba(31, 38, 52, 0.06);
    }}
    .message {{
      max-width: 980px;
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
    .mode strong {{
      color: var(--ink);
      font-weight: 700;
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
      grid-template-columns: auto minmax(180px, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
    }}
    .select-field {{
      min-width: 170px;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 0 10px;
      color: var(--ink);
      background: var(--surface-2);
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
    .agent-card {{
      border: 1px solid #d9ccff;
      border-radius: var(--radius);
      background: #fbf9ff;
      padding: 12px;
      margin-bottom: 12px;
    }}
    .agent-card-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .agent-card strong {{
      color: var(--ink);
      font-size: 14px;
    }}
    .agent-card span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .agent-card small {{
      display: block;
      margin-top: 8px;
      color: var(--soft);
      line-height: 1.4;
    }}
    .mini-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      background: #eefaf4;
      color: #257756;
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .timeline {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .panel-heading {{
      margin: 0 0 10px;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
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
    .surface-status {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 11px;
      margin-bottom: 10px;
    }}
    .surface-status h3 {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin: 0 0 8px;
      color: var(--ink);
      font-size: 13px;
    }}
    .surface-status h3 span {{
      color: var(--muted);
      font-weight: 600;
    }}
    .tag-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .tag {{
      max-width: 100%;
      min-height: 24px;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--surface-2);
      color: var(--muted);
      padding: 0 8px;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
    .knowledge-item .glyph {{
      display: inline-grid;
      place-items: center;
      width: 18px;
      height: 18px;
      border: 1px solid var(--line-strong);
      border-radius: 4px;
      color: var(--accent);
      background: var(--surface-2);
      font-size: 10px;
      font-weight: 800;
    }}
    .settings-note {{
      margin: 12px 0 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--muted);
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
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
      .run-summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ min-height: auto; }}
      .channel-list {{ display: none; }}
      .messages, .composer-wrap {{ padding-left: 16px; padding-right: 16px; }}
      .topbar {{ padding: 0 16px; }}
      .topbar-actions .pill:not(#chat-route-pill) {{ display: none; }}
      .starter-grid {{ grid-template-columns: 1fr; }}
      .quick-actions {{ grid-template-columns: 1fr; }}
      .surface-grid {{ grid-template-columns: 1fr; }}
      .run-state {{ grid-template-columns: 1fr; }}
      .trace-grid {{ grid-template-columns: 1fr; }}
      .health-rail {{ grid-template-columns: 1fr; }}
      .run-summary {{ grid-template-columns: 1fr; }}
      .composer-actions {{ grid-template-columns: 1fr; }}
      .select-field {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <script type="application/json" id="runtime-bootstrap">{bootstrap_json}</script>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>Magi Agent</h1>
        <div class="brand-meta"><span class="dot" id="runtime-dot"></span><span id="runtime-label">Checking runtime</span></div>
        <p class="brand-subtitle">Local dashboard for chat, work events, knowledge, first-party tools, and evidence receipts.</p>
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
        <div class="footer-row"><span>Runtime</span><strong id="footer-runtime">magi-agent</strong></div>
        <div class="footer-row"><span>Version</span><strong id="footer-version"></strong></div>
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
        <div class="thread-list" id="thread-list">
        <div class="empty-state" id="empty-state">
          <div class="welcome-message message assistant">
            <div class="thread-meta">
              <span><span class="dot ready"></span><span>Current run</span></span>
              <strong id="composer-status">Ready to run</strong>
            </div>
            <h3>Magi Agent is ready.</h3>
            <p>Use this local workspace for research, coding, document review, planning, and automation. Public runtime events and tool progress appear in the work stream while the answer streams here.</p>
            <div class="run-state" aria-label="Local runtime readiness">
              <div class="run-state-card"><span>Status</span><strong>No active run</strong></div>
              <div class="run-state-card"><span>Runtime</span><strong>ADK Python</strong></div>
              <div class="run-state-card"><span>Context</span><strong>Attach local context</strong></div>
            </div>
            <div class="surface-grid" aria-label="Runtime surfaces">
              <div class="surface-card"><strong>Runtime surfaces</strong><span>Chat, work events, SSE transport, and public ADK progress in one shell.</span></div>
              <div class="surface-card"><strong>First-party surfaces</strong><span>Research, coding, documents, browser, memory, scheduler, and skills.</span></div>
              <div class="surface-card"><strong>Evidence gates</strong><span>Receipts and policy status stay visible while local work runs.</span></div>
            </div>
            <div class="quick-actions" id="quick-actions">
            <button class="starter" type="button" data-prompt="Inspect this repository and summarize the runnable local surfaces.">Inspect this repository</button>
            <button class="starter" type="button" data-prompt="Draft a short research plan and list the evidence gates you would use.">Plan a research task</button>
            <button class="starter" type="button" data-prompt="Create a coding checklist for fixing a failing test, including rollback evidence.">Plan a coding fix</button>
            <button class="starter" type="button" data-prompt="Show the runtime health, active tools, and current policy boundaries.">Check runtime health</button>
            </div>
          </div>
        </div>
        </div>
      </section>

      <section class="composer-wrap">
        <form class="composer" id="chat-form">
          <div class="composer-strip">
            <span class="mode"><span class="dot ready"></span><strong>Live run</strong></span>
            <span class="mode">Streams ADK events, tool progress, and evidence when emitted</span>
          </div>
          <textarea id="prompt" placeholder="Ask the local agent to inspect, write, research, or plan..."></textarea>
          <div class="composer-actions">
            <select class="select-field" id="model-select" aria-label="Model">
              <option>local-dev</option>
            </select>
            <div class="token-field">
              <label for="gateway-token">Gateway token</label>
              <input id="gateway-token" type="password" autocomplete="current-password" placeholder="local-dev-token">
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
          <p class="panel-heading">Agents</p>
          <div class="agent-card">
            <div class="agent-card-head">
              <div>
                <strong>Main</strong>
                <span>current local session</span>
              </div>
              <span class="mini-pill" id="agent-state-pill">ready</span>
            </div>
            <small>Public ADK events, tool progress, evidence receipts, and transport state appear here during a run.</small>
          </div>
          <p class="panel-heading">Work in progress</p>
          <div class="event pending"><strong>No active run</strong><code>Submit a prompt to start a local ADK turn</code></div>
          <p class="panel-heading">Main session</p>
          <div class="timeline" id="work-stream-events">
            <div class="event pending"><strong>Runtime check</strong><code>Waiting for /healthz</code></div>
            <div class="event"><strong>First-party surfaces</strong><code>Research, coding, documents, browser, scheduler, memory, skills</code></div>
            <div class="event"><strong>Transport</strong><code>SSE frames and public ADK events render here during a run</code></div>
          </div>
        </div>
        <div id="panel-knowledge" class="hidden">
          <p class="brand-meta">Local knowledge and artifacts are exposed by runtime contracts when enabled.</p>
          <div class="surface-status">
            <h3>Active tools <span id="tool-count">checking</span></h3>
            <div class="tag-list" id="tool-list"><span class="tag">Waiting for /healthz</span></div>
          </div>
          <div class="surface-status">
            <h3>Harness packs <span>profile</span></h3>
            <div class="tag-list" id="harness-list"><span class="tag">Waiting for /healthz</span></div>
          </div>
          <div class="surface-status">
            <h3>Evidence gates <span>public-safe</span></h3>
            <div class="tag-list" id="evidence-list">
              <span class="tag">source ledger</span>
              <span class="tag">citation audit</span>
              <span class="tag">tool receipts</span>
              <span class="tag">final projection</span>
            </div>
          </div>
          <div class="knowledge-list">
            <div class="knowledge-item"><span class="glyph">F</span><span>Workspace files</span></div>
            <div class="knowledge-item"><span class="glyph">M</span><span>Memory receipts</span></div>
            <div class="knowledge-item"><span class="glyph">E</span><span>Evidence ledger</span></div>
            <div class="knowledge-item"><span class="glyph">A</span><span>Generated artifacts</span></div>
          </div>
        </div>
        <div id="panel-settings" class="hidden">
          <div class="kv">
            <div class="kv-row"><span>Runtime</span><strong id="settings-runtime">magi-agent</strong></div>
            <div class="kv-row"><span>Model</span><strong id="settings-model">local-dev</strong></div>
            <div class="kv-row"><span>Bot</span><strong id="settings-bot">local-bot</strong></div>
            <div class="kv-row"><span>Engine</span><strong id="settings-engine">adk-python</strong></div>
          </div>
          <p class="settings-note">Set GATEWAY_TOKEN before starting the server to require a custom local bearer token.</p>
        </div>
      </div>
    </aside>
  </div>

  <script>
    const bootstrap = JSON.parse(document.getElementById("runtime-bootstrap").textContent);
    const messages = document.getElementById("messages");
    const emptyState = document.getElementById("empty-state");
    const workStreamEvents = document.getElementById("work-stream-events");
    const form = document.getElementById("chat-form");
    const promptInput = document.getElementById("prompt");
    const tokenInput = document.getElementById("gateway-token");
    const sendButton = document.getElementById("send-button");
    const modelSelect = document.getElementById("model-select");
    const composerStatus = document.getElementById("composer-status");
    const runtimeDot = document.getElementById("runtime-dot");
    const runtimeLabel = document.getElementById("runtime-label");
    const runtimeKv = document.getElementById("runtime-kv");
    const chatRoutePill = document.getElementById("chat-route-pill");
    const tileRuntime = document.getElementById("tile-runtime");
    const tileState = document.getElementById("tile-state");
    const agentStatePill = document.getElementById("agent-state-pill");
    const toolCount = document.getElementById("tool-count");
    const toolList = document.getElementById("tool-list");
    const harnessList = document.getElementById("harness-list");
    const evidenceList = document.getElementById("evidence-list");
    const tokenKey = "magi-agent:gateway-token";

    document.getElementById("footer-runtime").textContent = bootstrap.runtime;
    document.getElementById("footer-version").textContent = bootstrap.version;
    document.getElementById("settings-runtime").textContent = bootstrap.runtime;
    document.getElementById("settings-model").textContent = bootstrap.model;
    document.getElementById("settings-bot").textContent = bootstrap.botId;
    document.getElementById("settings-engine").textContent = bootstrap.runtimeEngine || "adk-python";
    tileRuntime.textContent = bootstrap.runtime;
    modelSelect.innerHTML = `<option>${{escapeText(bootstrap.model)}}</option>`;
    tokenInput.value = localStorage.getItem(tokenKey) || bootstrap.gatewayToken || "";

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
      workStreamEvents.appendChild(node);
      workStreamEvents.scrollTop = workStreamEvents.scrollHeight;
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
      agentStatePill.textContent = ok ? "ready" : "blocked";
    }}

    function renderTagList(container, values, fallback) {{
      const items = Array.isArray(values) ? values.filter(Boolean).slice(0, 18) : [];
      const visible = items.length ? items : [fallback];
      container.innerHTML = "";
      for (const value of visible) {{
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = value;
        container.appendChild(tag);
      }}
    }}

    function renderSurfaceStatus(body) {{
      const activeTools = Array.isArray(body && body.activeTools) ? body.activeTools : [];
      toolCount.textContent = activeTools.length ? `${{activeTools.length}} active` : "none reported";
      renderTagList(toolList, activeTools, "No active tools reported");
      const profile = body && body.profile;
      const packs = profile && Array.isArray(profile.harnessPacks)
        ? profile.harnessPacks.map((pack) => `${{pack.name || "pack"}}:${{pack.enabledByDefault ? "on" : "off"}}`)
        : [];
      renderTagList(harnessList, packs, "No harness profile reported");
      const gates = [
        "source ledger",
        "citation audit",
        "tool receipts",
        "rollback receipts",
        "final projection",
      ];
      renderTagList(evidenceList, gates, "Evidence gates unavailable");
    }}

    async function checkHealth() {{
      try {{
        const response = await fetch("/healthz");
        const body = await response.json();
        setHealth(response.ok, response.ok ? "active" : "blocked");
        renderHealth(body, response.ok);
        renderSurfaceStatus(body);
        addEvent("Runtime health", compactJson({{ ok: response.ok, status: body.status || "ready" }}), response.ok ? "ok" : "error");
      }} catch (error) {{
        setHealth(false, "unavailable");
        chatRoutePill.textContent = "runtime unavailable";
        tileState.textContent = "unavailable";
        agentStatePill.textContent = "unavailable";
        renderSurfaceStatus({{}});
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
      composerStatus.textContent = "Running";
      agentStatePill.textContent = "running";
      addEvent("Request", "POST /v1/chat/completions", "pending");
      const response = await fetch("/v1/chat/completions", {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}),
        }},
        body: JSON.stringify({{
          model: modelSelect.value || bootstrap.model,
          messages: [{{ role: "user", content: prompt }}],
          stream: true,
        }}),
      }});
      if (!response.ok) {{
        const text = await response.text();
        assistant.className = "message assistant error";
        assistant.textContent = text || `Request failed: ${{response.status}}`;
        addEvent("Request failed", assistant.textContent, "error");
        composerStatus.textContent = "Blocked";
        agentStatePill.textContent = "blocked";
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
      composerStatus.textContent = "Ready to run";
      agentStatePill.textContent = "ready";
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
