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
      color-scheme: dark;
      --canvas: #0f141c;
      --bg: var(--canvas);
      --surface: #151a21;
      --surface-2: #1a2029;
      --surface-3: #242c36;
      --surface-4: #111a18;
      --ink: #edf3ef;
      --muted: #aab4b7;
      --soft: #76838c;
      --line: #2e3742;
      --line-strong: #46505d;
      --accent: #21c17a;
      --accent-soft: rgba(33, 193, 122, 0.14);
      --accent-line: rgba(33, 193, 122, 0.38);
      --green: #21c17a;
      --red: #ff6b7a;
      --amber: #efbd5a;
      --focus: #efbd5a;
      --shadow: 0 20px 58px rgba(0, 0, 0, 0.34);
      --shadow-soft: 0 10px 30px rgba(0, 0, 0, 0.22);
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; overflow: hidden; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    button, input, textarea, select {{ font: inherit; letter-spacing: 0; }}
    button {{ cursor: pointer; }}
    button:disabled {{ cursor: not-allowed; }}
    :focus-visible {{
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }}
    .app {{
      display: grid;
      grid-template-columns: 304px minmax(0, 1fr) 408px;
      height: 100vh;
      min-height: 720px;
      background: var(--canvas);
    }}
    .sidebar {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 0;
      background: #10161f;
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
    .channel-count {{
      min-width: 22px;
      height: 20px;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #10161f;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
    }}
    .channel.active .channel-count {{
      border-color: var(--accent-line);
      color: var(--accent);
      background: var(--surface-4);
    }}
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
      min-height: 0;
      background: var(--canvas);
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 66px;
      padding: 0 24px;
      background: #121922;
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
      transition: background 150ms ease, border-color 150ms ease, color 150ms ease;
    }}
    .icon-button:hover {{ color: var(--ink); border-color: var(--line-strong); }}
    .icon-button svg {{
      width: 16px;
      height: 16px;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }}
    .icon-button.danger {{
      color: var(--red);
      border-color: rgba(255, 107, 122, 0.34);
      background: rgba(255, 107, 122, 0.08);
    }}
    .icon-button.danger:disabled {{
      opacity: 0.42;
    }}
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
      min-height: 0;
      padding: 28px min(7vw, 78px);
      display: flex;
      flex-direction: column;
      gap: 18px;
      background: var(--canvas);
    }}
    .chat-viewport {{
      scroll-padding-bottom: 132px;
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
      margin: auto;
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
      padding: 20px;
    }}
    .workspace-board {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(260px, 0.8fr);
      gap: 12px;
      margin-top: 14px;
    }}
    .board-panel {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface-2);
      padding: 12px;
      min-width: 0;
    }}
    .board-panel h4 {{
      margin: 0 0 10px;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .run-row {{
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-height: 34px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }}
    .run-row:first-of-type {{ border-top: 0; }}
    .run-row strong {{
      color: var(--ink);
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .run-row code {{
      color: var(--soft);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 11px;
    }}
    .receipt-list {{
      display: grid;
      gap: 8px;
    }}
    .receipt {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 0 10px;
      color: var(--muted);
      font-size: 12px;
    }}
    .receipt strong {{
      color: var(--ink);
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .welcome-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 28px;
      padding: 0 10px;
      margin-bottom: 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .thread-list {{
      max-width: 980px;
      width: 100%;
      margin: 0 auto;
      display: grid;
      gap: 12px;
    }}
    .message-stack {{
      align-content: start;
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
      border: 1px solid var(--accent-line);
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
      border-color: rgba(255, 107, 122, 0.42);
      background: rgba(255, 107, 122, 0.12);
      color: #ffc7ce;
    }}
    .composer-wrap {{
      padding: 16px min(7vw, 78px) 22px;
      background: linear-gradient(180deg, rgba(15, 20, 28, 0), var(--canvas) 18%);
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
    .composer-toolbar {{
      background: var(--surface-2);
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
      min-height: 0;
      background: #10161f;
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
      border: 1px solid var(--accent-line);
      border-radius: var(--radius);
      background: var(--accent-soft);
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
      background: rgba(33, 193, 122, 0.16);
      color: #9ff0c8;
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
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
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
      padding: 11px 12px;
      font-size: 12px;
      color: var(--muted);
    }}
    .event.pending {{
      border-color: var(--accent-line);
      background: var(--accent-soft);
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
    .event-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }}
    .event-metric {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      min-height: 58px;
      padding: 10px;
    }}
    .event-metric span {{
      display: block;
      color: var(--soft);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .event-metric strong {{
      display: block;
      margin-top: 5px;
      color: var(--ink);
      font-size: 13px;
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
    .source-list,
    .control-request-list {{
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }}
    .source-item,
    .control-request {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .source-item strong,
    .control-request strong {{
      display: block;
      margin-bottom: 4px;
      color: var(--ink);
      font-size: 13px;
    }}
    .control-request {{
      border-color: var(--accent-line);
      background: var(--accent-soft);
    }}
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
    @media (prefers-reduced-motion: reduce) {{
      *,
      *::before,
      *::after {{
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        scroll-behavior: auto !important;
        transition-duration: 0.001ms !important;
      }}
    }}
    @media (max-width: 1120px) {{
      .app {{ grid-template-columns: 220px minmax(0, 1fr); }}
      html, body {{ overflow: auto; }}
      .app {{ height: auto; min-height: 100vh; }}
      .inspector {{ grid-column: 1 / -1; min-height: 420px; border-left: 0; border-top: 1px solid var(--line); }}
      .run-summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      html, body {{ width: 100%; overflow-x: hidden; }}
      .app {{ width: 100%; overflow-x: hidden; }}
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ min-height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      .brand {{ padding: 18px 16px; }}
      .brand-subtitle {{ max-width: 32ch; }}
      .channel-list {{ display: none; }}
      .sidebar-footer {{ display: none; }}
      .main, .messages, .empty-state, .welcome-message, .inspector, .composer {{ min-width: 0; max-width: 100%; }}
      .messages, .composer-wrap {{ padding-left: 16px; padding-right: 16px; }}
      .topbar {{ min-height: auto; padding: 12px 16px; align-items: stretch; flex-direction: column; gap: 10px; }}
      .topbar-title, .topbar-actions {{ width: 100%; }}
      .topbar-title p {{ white-space: normal; }}
      .topbar-actions {{ max-width: 100%; flex-wrap: wrap; justify-content: flex-start; gap: 8px; }}
      .topbar-actions .pill:not(#chat-route-pill) {{ display: none; }}
      .pill {{ min-width: 0; max-width: 100%; }}
      .message {{ max-width: 100%; }}
      .welcome-message {{ padding: 16px; }}
      .starter-grid {{ grid-template-columns: 1fr; }}
      .quick-actions {{ grid-template-columns: 1fr; }}
      .surface-grid {{ grid-template-columns: 1fr; }}
      .workspace-board {{ grid-template-columns: 1fr; }}
      .run-row {{ grid-template-columns: 18px minmax(0, 1fr); align-items: start; padding: 6px 0; }}
      .run-row code {{ grid-column: 2; }}
      .run-state {{ grid-template-columns: 1fr; }}
      .trace-grid {{ grid-template-columns: 1fr; }}
      .health-rail {{ grid-template-columns: 1fr; }}
      .run-summary {{ grid-template-columns: 1fr; }}
      .event-grid {{ grid-template-columns: 1fr; }}
      .composer-strip {{ align-items: flex-start; flex-direction: column; padding: 12px 14px; }}
      .composer-actions {{ grid-template-columns: 1fr; }}
      .select-field {{ width: 100%; }}
      .token-field {{ align-items: stretch; flex-direction: column; gap: 6px; }}
      .send {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <script type="application/json" id="runtime-bootstrap">{bootstrap_json}</script>
  <div class="app" data-dashboard-shell="local-chat" data-visual-system="agent-console">
    <aside class="sidebar" data-shell-region="channels">
      <div class="brand">
        <h1>Magi Agent</h1>
        <div class="brand-meta"><span class="dot" id="runtime-dot"></span><span id="runtime-label">Checking runtime</span></div>
        <p class="brand-subtitle">Local workspace for chat, work events, knowledge, first-party tools, and evidence receipts.</p>
      </div>
      <nav class="channel-list" id="channel-nav" aria-label="Local channels">
        <p class="section-label">General</p>
        <button class="channel active" type="button" data-channel="general" data-summary="Local Magi Agent workspace"><span>#</span><span>general</span><span class="channel-count">1</span></button>
        <button class="channel" type="button" data-channel="research" data-summary="Research plans, sources, and evidence gates"><span>#</span><span>research</span><span class="channel-count">0</span></button>
        <button class="channel" type="button" data-channel="coding" data-summary="Coding tasks, tool receipts, patches, and verification"><span>#</span><span>coding</span><span class="channel-count">0</span></button>
        <button class="channel" type="button" data-channel="automation" data-summary="Local workflows, schedules, and background work"><span>#</span><span>automation</span><span class="channel-count">0</span></button>
        <p class="section-label" style="margin-top:18px">Runtime</p>
        <button class="channel" type="button" data-channel="memory" data-summary="Memory status and recall receipts"><span>*</span><span>Memory</span><span class="channel-count">on</span></button>
        <button class="channel" type="button" data-channel="tools" data-summary="First-party local tools and harness packs"><span>*</span><span>Tools</span><span class="channel-count">72</span></button>
        <button class="channel" type="button" data-channel="evidence" data-summary="Evidence, receipts, and final projection gates"><span>*</span><span>Evidence</span><span class="channel-count">5</span></button>
      </nav>
      <div class="sidebar-footer">
        <div class="footer-row"><span>Runtime</span><strong id="footer-runtime">magi-agent</strong></div>
        <div class="footer-row"><span>Version</span><strong id="footer-version"></strong></div>
      </div>
    </aside>

    <main class="main" data-shell-region="transcript">
      <header class="topbar">
        <div class="topbar-title">
          <h2 id="selected-channel-title"># general</h2>
          <p id="selected-channel-summary">Local Magi Agent workspace</p>
        </div>
        <div class="topbar-actions" aria-label="Conversation controls">
          <span class="pill" id="active-session-id">local session</span>
          <span class="pill" id="transport-status">transport idle</span>
          <button class="icon-button danger" type="button" title="Cancel current run" aria-label="Cancel current run" id="cancel-run" disabled>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12"></path><path d="M18 6L6 18"></path></svg>
          </button>
          <button class="icon-button" type="button" title="Refresh runtime health" aria-label="Refresh runtime health" id="refresh-health">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"></path><path d="M21 3v6h-6"></path></svg>
          </button>
          <span class="pill"><span class="dot ready"></span><span>ADK runtime</span></span>
          <span class="pill" id="chat-route-pill">chat route unknown</span>
        </div>
      </header>

      <section class="messages chat-viewport" id="messages" aria-label="Chat transcript" aria-live="polite">
        <div class="thread-list message-stack" id="thread-list">
        <div class="empty-state" id="empty-state">
          <div class="welcome-message message assistant">
            <div class="welcome-kicker"><span class="dot ready"></span><span>Local workspace ready</span></div>
            <div class="thread-meta">
              <span><span class="dot ready"></span><span>Current run</span></span>
              <strong id="composer-status">Ready to run</strong>
            </div>
            <h3>Magi Agent is ready.</h3>
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
            <div class="workspace-board" id="workspace-board">
              <div class="board-panel">
                <h4>Workload</h4>
                <div class="run-row"><span class="dot ready"></span><strong>General agent turn</strong><code id="board-turn-state">idle</code></div>
                <div class="run-row"><span class="dot"></span><strong>Tool progress stream</strong><code id="board-tool-state">waiting</code></div>
                <div class="run-row"><span class="dot"></span><strong>Evidence receipt stream</strong><code id="board-evidence-state">waiting</code></div>
                <div class="run-row"><span class="dot"></span><strong>Transport channel</strong><code id="board-transport-state">ready</code></div>
              </div>
              <div class="board-panel">
                <h4>Receipts</h4>
                <div class="receipt-list" id="receipt-list">
                  <div class="receipt"><strong>request</strong><span>pending</span></div>
                  <div class="receipt"><strong>delivery</strong><span>pending</span></div>
                  <div class="receipt"><strong>tool</strong><span>pending</span></div>
                </div>
              </div>
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

      <section class="composer-wrap" data-shell-region="composer">
        <form class="composer" id="chat-form" aria-label="Prompt composer">
          <div class="composer-strip composer-toolbar">
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

    <aside class="inspector" data-shell-region="workbench">
      <div class="inspector-head">
        <h2>Work Stream</h2>
        <div class="brand-meta">Runtime events, tool progress, evidence, and SSE state.</div>
        <div class="status-band">
          <div class="status-tile"><span>Runtime</span><strong id="tile-runtime">magi-agent</strong></div>
          <div class="status-tile"><span>State</span><strong id="tile-state">checking</strong></div>
        </div>
        <div class="kv" id="runtime-kv"></div>
      </div>
      <div class="tabs" role="tablist" aria-label="Workspace tabs">
        <button class="tab active" type="button" role="tab" aria-selected="true" aria-controls="panel-work" data-panel="work">Work</button>
        <button class="tab" type="button" role="tab" aria-selected="false" aria-controls="panel-knowledge" data-panel="knowledge">Knowledge</button>
        <button class="tab" type="button" role="tab" aria-selected="false" aria-controls="panel-settings" data-panel="settings">Settings</button>
      </div>
      <div class="panel">
        <div role="tabpanel" id="panel-work" class="timeline">
          <p class="panel-heading"><span>Agents</span><span class="mini-pill">1 agent</span></p>
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
          <p class="panel-heading"><span>Work in progress</span></p>
          <div class="event-grid" aria-label="Run metrics">
            <div class="event-metric"><span>SSE</span><strong id="metric-sse">0 frames</strong></div>
            <div class="event-metric"><span>Events</span><strong id="metric-events">0 agent events</strong></div>
            <div class="event-metric"><span>Tools</span><strong id="metric-tools">idle</strong></div>
            <div class="event-metric"><span>Receipts</span><strong id="metric-receipts">pending</strong></div>
          </div>
          <div class="event pending"><strong>No active run</strong><code>Submit a prompt to start a local ADK turn</code></div>
          <p class="panel-heading">Main session</p>
          <div class="timeline" id="work-stream-events">
            <div class="event pending"><strong>Runtime check</strong><code>Waiting for /healthz</code></div>
            <div class="event"><strong>First-party surfaces</strong><code>Research, coding, documents, browser, scheduler, memory, skills</code></div>
            <div class="event"><strong>Transport</strong><code>SSE frames and public ADK events render here during a run</code></div>
          </div>
          <p class="panel-heading">Control requests</p>
          <div class="control-request-list" id="control-request-list">
            <div class="control-request"><strong>No pending control</strong><span>Approval and interrupt requests appear here.</span></div>
          </div>
        </div>
        <div role="tabpanel" id="panel-knowledge" class="hidden">
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
          <div class="surface-status">
            <h3>Inspected sources <span id="source-count">0</span></h3>
            <div class="source-list" id="source-list">
              <div class="source-item"><strong>No sources yet</strong><span>Source inspection events appear during research turns.</span></div>
            </div>
          </div>
          <div class="knowledge-list">
            <div class="knowledge-item"><span class="glyph">F</span><span>Workspace files</span></div>
            <div class="knowledge-item"><span class="glyph">M</span><span>Memory receipts</span></div>
            <div class="knowledge-item"><span class="glyph">E</span><span>Evidence ledger</span></div>
            <div class="knowledge-item"><span class="glyph">A</span><span>Generated artifacts</span></div>
          </div>
        </div>
        <div role="tabpanel" id="panel-settings" class="hidden">
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
    const cancelButton = document.getElementById("cancel-run");
    const modelSelect = document.getElementById("model-select");
    const composerStatus = document.getElementById("composer-status");
    const activeSessionId = document.getElementById("active-session-id");
    const transportStatus = document.getElementById("transport-status");
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
    const metricSse = document.getElementById("metric-sse");
    const metricEvents = document.getElementById("metric-events");
    const metricTools = document.getElementById("metric-tools");
    const metricReceipts = document.getElementById("metric-receipts");
    const boardTurnState = document.getElementById("board-turn-state");
    const boardToolState = document.getElementById("board-tool-state");
    const boardEvidenceState = document.getElementById("board-evidence-state");
    const boardTransportState = document.getElementById("board-transport-state");
    const receiptList = document.getElementById("receipt-list");
    const selectedChannelTitle = document.getElementById("selected-channel-title");
    const selectedChannelSummary = document.getElementById("selected-channel-summary");
    const sourceList = document.getElementById("source-list");
    const sourceCount = document.getElementById("source-count");
    const controlRequestList = document.getElementById("control-request-list");
    const tokenKey = "magi-agent:gateway-token";
    const streamChatEndpoint = "/v1/chat/stream";
    const legacyChatEndpoint = "/v1/chat/completions";
    const controlResponseEndpoint = "/v1/chat/control-response";
    const cancelEndpoint = "/v1/chat/cancel";
    let sseFrameCount = 0;
    let agentEventCount = 0;
    let currentTurnId = null;
    let inspectedSources = [];

    activeSessionId.textContent = `${{bootstrap.botId}}:local-dashboard:general`;
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
      if (tone === "error") node.style.borderColor = "rgba(255, 107, 122, 0.52)";
      if (tone === "ok") node.style.borderColor = "rgba(33, 193, 122, 0.48)";
      workStreamEvents.appendChild(node);
      workStreamEvents.scrollTop = workStreamEvents.scrollHeight;
      return node;
    }}

    function setRunBoard(state) {{
      const isRunning = state === "running";
      const isBlocked = state === "blocked";
      boardTurnState.textContent = isRunning ? "running" : isBlocked ? "blocked" : "idle";
      boardToolState.textContent = isRunning ? "streaming" : isBlocked ? "blocked" : "waiting";
      boardEvidenceState.textContent = isRunning ? "collecting" : isBlocked ? "blocked" : "ready";
      boardTransportState.textContent = isRunning ? "open" : isBlocked ? "closed" : "ready";
      metricTools.textContent = isRunning ? "watching" : isBlocked ? "blocked" : "idle";
      metricReceipts.textContent = isRunning ? "collecting" : isBlocked ? "blocked" : "ready";
      transportStatus.textContent = isRunning ? "stream open" : isBlocked ? "transport blocked" : "transport idle";
    }}

    function renderReceiptList(receipts) {{
      receiptList.innerHTML = "";
      for (const [name, status] of receipts) {{
        const node = document.createElement("div");
        node.className = "receipt";
        node.innerHTML = `<strong>${{escapeText(name)}}</strong><span>${{escapeText(status)}}</span>`;
        receiptList.appendChild(node);
      }}
    }}

    function renderSourceList(sources) {{
      inspectedSources = Array.isArray(sources) ? sources.slice(-12) : inspectedSources;
      sourceCount.textContent = String(inspectedSources.length);
      sourceList.innerHTML = "";
      if (!inspectedSources.length) {{
        const node = document.createElement("div");
        node.className = "source-item";
        node.innerHTML = "<strong>No sources yet</strong><span>Source inspection events appear during research turns.</span>";
        sourceList.appendChild(node);
        return;
      }}
      for (const source of inspectedSources) {{
        const title = source.title || source.url || source.sourceId || "Source";
        const detail = source.url || source.domain || source.status || "inspected";
        const node = document.createElement("div");
        node.className = "source-item";
        node.innerHTML = `<strong>${{escapeText(title)}}</strong><span>${{escapeText(detail)}}</span>`;
        sourceList.appendChild(node);
      }}
    }}

    function renderControlRequest(payload) {{
      controlRequestList.innerHTML = "";
      const node = document.createElement("div");
      node.className = "control-request";
      const action = payload.action || payload.requestedAction || payload.type || "Control request";
      const reason = payload.reason || payload.message || payload.summary || "Runtime is waiting for local operator input.";
      node.innerHTML = `<strong>${{escapeText(action)}}</strong><span>${{escapeText(reason)}}</span>`;
      controlRequestList.appendChild(node);
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

    function classifyAgentEvent(payload) {{
      const type = payload && (payload.type || payload.eventType || payload.status || "agent");
      const titleByType = {{
        turn_start: "Turn started",
        turn_end: "Turn ended",
        turn_phase: "Turn phase",
        tool_start: "Tool started",
        tool_end: "Tool completed",
        tool_error: "Tool failed",
        evidence_receipt: "Evidence receipt",
        control_request: "Control request",
        source_inspected: "Source inspected",
        rule_check: "Rule check",
        llm_progress: "Model progress",
        patch_preview: "Patch preview",
        coding_final_projection: "Coding projection",
        research_final_projection: "Research projection",
        runtime_trace: "Runtime trace",
        error: "Runtime error",
      }};
      const title = titleByType[type] || String(type).replace(/_/g, " ");
      const tone = type === "error" || type === "tool_error" ? "error" : type === "control_request" ? "pending" : "ok";
      return {{ type, title, tone }};
    }}

    function renderAgentEvent(payload, eventName) {{
      const data = payload && typeof payload === "object" ? payload : {{ value: payload }};
      const event = classifyAgentEvent(data);
      agentEventCount += 1;
      metricEvents.textContent = `${{agentEventCount}} agent event${{agentEventCount === 1 ? "" : "s"}}`;
      if (event.type === "tool_start" || event.type === "tool_end" || event.type === "tool_error") {{
        metricTools.textContent = event.title;
      }}
      if (event.type === "evidence_receipt" || Array.isArray(data.receiptRefs)) {{
        metricReceipts.textContent = "received";
        const receiptRefs = Array.isArray(data.receiptRefs) ? data.receiptRefs : [data.receiptRef || "evidence"];
        renderReceiptList(receiptRefs.filter(Boolean).map((ref) => [String(ref).split(":")[0], "received"]));
      }}
      if (event.type === "source_inspected" || data.source || data.url) {{
        const source = data.source && typeof data.source === "object" ? data.source : data;
        const sourceId = source.sourceId || source.url || `${{Date.now()}}`;
        inspectedSources = inspectedSources.filter((item) => item.sourceId !== sourceId && item.url !== source.url);
        inspectedSources.push({{ ...source, sourceId }});
        renderSourceList(inspectedSources);
      }}
      if (event.type === "control_request") {{
        renderControlRequest(data);
      }}
      addEvent(event.title || `event: ${{eventName}}`, compactJson(data), event.tone);
    }}

    function renderSseBlock(target, block) {{
      sseFrameCount += 1;
      metricSse.textContent = `${{sseFrameCount}} frame${{sseFrameCount === 1 ? "" : "s"}}`;
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
        renderReceiptList([["request", "sent"], ["delivery", "served"], ["transport", "done"]]);
        return true;
      }}
      try {{
        const parsed = JSON.parse(rawData);
        appendDelta(target, parsed);
        if (eventName === "agent") {{
          renderAgentEvent(parsed, eventName);
        }} else if (eventName !== "message" || parsed.type || parsed.event || parsed.status) {{
          renderAgentEvent(parsed, eventName);
        }}
      }} catch (error) {{
        addEvent(`event: ${{eventName}}`, rawData);
      }}
      return false;
    }}

    async function postChatStream(endpoint, token, prompt, turnId) {{
      return fetch(endpoint, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}),
        }},
        body: JSON.stringify({{
          model: modelSelect.value || bootstrap.model,
          messages: [{{ role: "user", content: prompt }}],
          sessionId: `${{bootstrap.botId}}:local-dashboard:general`,
          turnId: turnId || `${{Date.now()}}:${{Math.random().toString(16).slice(2)}}`,
          stream: true,
        }}),
      }});
    }}

    async function cancelCurrentRun() {{
      if (!currentTurnId) return;
      const token = tokenInput.value.trim();
      cancelButton.disabled = true;
      transportStatus.textContent = "cancelling";
      addEvent("Cancel requested", `POST ${{cancelEndpoint}}`, "pending");
      try {{
        const response = await fetch(cancelEndpoint, {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json",
            ...(token ? {{ "Authorization": `Bearer ${{token}}` }} : {{}}),
          }},
          body: JSON.stringify({{
            sessionId: `${{bootstrap.botId}}:local-dashboard:general`,
            turnId: currentTurnId,
          }}),
        }});
        if (!response.ok) {{
          const text = await response.text();
          addEvent("Cancel failed", text || `Request failed: ${{response.status}}`, "error");
          transportStatus.textContent = "cancel blocked";
          return;
        }}
        composerStatus.textContent = "Cancelling";
        agentStatePill.textContent = "cancelling";
        transportStatus.textContent = "cancel sent";
        addEvent("Cancel sent", currentTurnId, "ok");
      }} catch (error) {{
        addEvent("Cancel unavailable", "Could not reach /v1/chat/cancel", "error");
        transportStatus.textContent = "cancel unavailable";
      }}
    }}

    async function sendPrompt(prompt) {{
      const token = tokenInput.value.trim();
      localStorage.setItem(tokenKey, token);
      const assistant = addMessage("assistant", "");
      currentTurnId = `${{Date.now()}}:${{Math.random().toString(16).slice(2)}}`;
      sseFrameCount = 0;
      agentEventCount = 0;
      metricSse.textContent = "0 frames";
      metricEvents.textContent = "0 agent events";
      composerStatus.textContent = "Running";
      agentStatePill.textContent = "running";
      cancelButton.disabled = false;
      setRunBoard("running");
      renderReceiptList([["request", "sending"], ["delivery", "pending"], ["transport", "opening"]]);
      addEvent("Request", "POST /v1/chat/stream", "pending");
      let response = await postChatStream(streamChatEndpoint, token, prompt, currentTurnId);
      if (!response.ok && response.status === 503) {{
        const preview = await response.clone().text();
        if (preview.includes("streaming_chat_disabled")) {{
          addEvent("Streaming route disabled", "Falling back to POST /v1/chat/completions", "pending");
          response = await postChatStream(legacyChatEndpoint, token, prompt, currentTurnId);
        }}
      }}
      if (!response.ok) {{
        const text = await response.text();
        assistant.className = "message assistant error";
        assistant.textContent = text || `Request failed: ${{response.status}}`;
        addEvent("Request failed", assistant.textContent, "error");
        composerStatus.textContent = "Blocked";
        agentStatePill.textContent = "blocked";
        cancelButton.disabled = true;
        currentTurnId = null;
        setRunBoard("blocked");
        renderReceiptList([["request", "failed"], ["delivery", "blocked"], ["transport", String(response.status)]]);
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
      cancelButton.disabled = true;
      currentTurnId = null;
      setRunBoard("ready");
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

    function activateChannel(channelName) {{
      for (const channel of document.querySelectorAll("[data-channel]")) {{
        const active = channel.dataset.channel === channelName;
        channel.classList.toggle("active", active);
        if (active) {{
          selectedChannelTitle.textContent = channelName === "general" ? "# general" : channelName;
          selectedChannelSummary.textContent = channel.dataset.summary || "Local Magi Agent workspace";
        }}
      }}
      if (channelName === "tools" || channelName === "memory" || channelName === "evidence") {{
        document.querySelector('[data-panel="knowledge"]').click();
      }}
    }}

    for (const channel of document.querySelectorAll("[data-channel]")) {{
      channel.addEventListener("click", () => activateChannel(channel.dataset.channel || "general"));
    }}

    for (const tab of document.querySelectorAll(".tab")) {{
      tab.addEventListener("click", () => {{
        for (const current of document.querySelectorAll(".tab")) {{
          current.classList.remove("active");
          current.setAttribute("aria-selected", "false");
        }}
        tab.classList.add("active");
        tab.setAttribute("aria-selected", "true");
        for (const id of ["work", "knowledge", "settings"]) {{
          document.getElementById(`panel-${{id}}`).classList.toggle("hidden", id !== tab.dataset.panel);
        }}
      }});
    }}

    document.getElementById("refresh-health").addEventListener("click", () => {{
      checkHealth();
    }});

    cancelButton.addEventListener("click", () => {{
      cancelCurrentRun();
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
