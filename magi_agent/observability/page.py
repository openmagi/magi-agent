from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Magi Observability</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --surface: #ffffff;
      --surface-2: #fbfcff;
      --surface-3: #eef1f7;
      --ink: #222736;
      --muted: #6d7484;
      --soft: #9aa3b5;
      --line: #dde2eb;
      --line-strong: #cfd6e2;
      --accent: #7047d8;
      --accent-soft: #efe8ff;
      --green: #2fbf7b;
      --red: #d9495f;
      --radius: 8px;
      --shadow-soft: 0 5px 18px rgba(31,38,52,0.05);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; background: var(--bg); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; }
    button, input { font: inherit; cursor: pointer; }
    .page { display: flex; flex-direction: column; min-height: 100vh; }
    .header {
      display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
      padding: 14px 24px; background: var(--surface);
      border-bottom: 1px solid var(--line); box-shadow: var(--shadow-soft);
    }
    .header h1 { font-size: 17px; font-weight: 700; flex: none; }
    .header-sep { flex: 1; }
    .token-wrap { display: flex; align-items: center; gap: 8px; }
    .token-wrap label { color: var(--soft); font-size: 12px; white-space: nowrap; }
    .token-wrap input {
      height: 32px; min-width: 220px; border: 1px solid var(--line);
      border-radius: var(--radius); padding: 0 10px; background: var(--surface-2);
      color: var(--ink);
    }
    .meta-line {
      padding: 6px 24px; font-size: 12px; color: var(--muted);
      background: var(--surface-3); border-bottom: 1px solid var(--line);
      min-height: 30px; display: flex; align-items: center; gap: 6px;
    }
    .meta-line strong { color: var(--ink); }
    .error-banner {
      display: none; padding: 8px 24px; background: #fff4f6; color: #8a2638;
      border-bottom: 1px solid #f2bfca; font-size: 13px;
    }
    .tab-bar {
      display: flex; gap: 0; background: var(--surface);
      border-bottom: 1px solid var(--line); padding: 0 24px;
    }
    .tab-btn {
      height: 42px; padding: 0 18px; border: 0; border-bottom: 2px solid transparent;
      background: transparent; color: var(--muted); font-weight: 600; font-size: 13px;
    }
    .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
    .tab-btn:hover:not(.active) { color: var(--ink); }
    .content { flex: 1; padding: 24px; overflow: auto; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    /* Live tab */
    .live-controls { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
    .btn {
      height: 32px; padding: 0 14px; border: 1px solid var(--line);
      border-radius: var(--radius); background: var(--surface); color: var(--ink);
      font-weight: 600; font-size: 13px;
    }
    .btn.primary { background: var(--accent); color: white; border-color: var(--accent); }
    .btn:hover:not(.primary) { border-color: var(--line-strong); }
    .event-list {
      max-height: 70vh; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
      border: 1px solid var(--line); border-radius: var(--radius); padding: 8px;
      background: var(--surface);
    }
    .event-row {
      display: grid; grid-template-columns: 90px 110px 130px 110px 1fr;
      gap: 8px; align-items: baseline; padding: 5px 8px;
      border-radius: 5px; font-size: 12px; color: var(--muted);
      border: 1px solid transparent;
    }
    .event-row:hover { background: var(--surface-3); }
    .event-time { font-family: ui-monospace, Menlo, monospace; color: var(--soft); }
    .event-kind { font-weight: 700; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .event-tool { color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .event-status { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .event-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
    .event-empty { padding: 16px; color: var(--soft); text-align: center; font-size: 13px; }
    /* Sessions tab */
    .sessions-table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow-soft); }
    th { text-align: left; padding: 10px 14px; background: var(--surface-3); color: var(--soft); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--line); }
    td { padding: 10px 14px; border-bottom: 1px solid var(--line); font-size: 13px; color: var(--ink); }
    tr:last-child td { border-bottom: 0; }
    tr.clickable { cursor: pointer; }
    tr.clickable:hover td { background: var(--accent-soft); }
    .session-detail {
      margin-top: 18px; border: 1px solid var(--line); border-radius: var(--radius);
      background: var(--surface); padding: 14px;
    }
    .session-detail h3 { font-size: 14px; margin-bottom: 10px; color: var(--muted); }
    .session-events { display: flex; flex-direction: column; gap: 4px; max-height: 50vh; overflow-y: auto; }
    /* Health tab */
    .health-pre {
      background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
      padding: 16px; font-family: ui-monospace, Menlo, monospace; font-size: 12px;
      line-height: 1.6; white-space: pre-wrap; word-break: break-all;
      max-height: 75vh; overflow: auto;
    }
    /* Board tab */
    .board-content {
      background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
      padding: 16px; font-family: ui-monospace, Menlo, monospace; font-size: 12px;
      line-height: 1.6; white-space: pre-wrap; word-break: break-all;
      max-height: 75vh; overflow: auto;
    }
    .section-title { font-size: 13px; font-weight: 700; color: var(--soft); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 999px; background: var(--soft); }
    .dot.green { background: var(--green); }
    .dot.red { background: var(--red); }
    @media (max-width: 700px) {
      .event-row { grid-template-columns: 80px 1fr; }
      .event-tool, .event-status, .event-summary { display: none; }
    }
  </style>
</head>
<body>
<div class="page">
  <header class="header">
    <h1>Magi Observability</h1>
    <span class="header-sep"></span>
    <div class="token-wrap">
      <label for="obs-token">Gateway token</label>
      <input id="obs-token" type="password" autocomplete="current-password" value="local-dev-token">
    </div>
  </header>

  <div class="meta-line" id="meta-line">
    <span class="dot" id="meta-dot"></span>
    <span id="meta-text">Loading...</span>
  </div>

  <div class="error-banner" id="error-banner"></div>

  <div class="tab-bar" role="tablist">
    <button class="tab-btn active" type="button" data-tab="live">Live</button>
    <button class="tab-btn" type="button" data-tab="sessions">Sessions</button>
    <button class="tab-btn" type="button" data-tab="health">Health</button>
    <button class="tab-btn" type="button" data-tab="board">Board</button>
  </div>

  <div class="content">
    <!-- Live tab -->
    <div class="tab-panel active" id="tab-live">
      <div class="live-controls">
        <p class="section-title" style="margin:0">Activity Feed</p>
        <button class="btn primary" id="toggle-poll" type="button">Stop</button>
        <button class="btn" id="clear-events" type="button">Clear</button>
      </div>
      <div class="event-list" id="event-list">
        <div class="event-empty" id="event-list-empty">No events yet. Polling for activity...</div>
      </div>
    </div>

    <!-- Sessions tab -->
    <div class="tab-panel" id="tab-sessions">
      <p class="section-title">Sessions</p>
      <div class="sessions-table-wrap">
        <table id="sessions-table">
          <thead><tr>
            <th>Session ID</th>
            <th>Events</th>
            <th>Tools</th>
            <th>Last Active</th>
          </tr></thead>
          <tbody id="sessions-tbody">
            <tr><td colspan="4" style="color:var(--soft);text-align:center;padding:20px">Loading sessions...</td></tr>
          </tbody>
        </table>
      </div>
      <div class="session-detail" id="session-detail" style="display:none">
        <h3 id="session-detail-title">Session events</h3>
        <div class="session-events" id="session-events"></div>
      </div>
    </div>

    <!-- Health tab -->
    <div class="tab-panel" id="tab-health">
      <p class="section-title">Health</p>
      <pre class="health-pre" id="health-pre">Loading...</pre>
    </div>

    <!-- Board tab -->
    <div class="tab-panel" id="tab-board">
      <p class="section-title">Board</p>
      <div class="board-content" id="board-content">Loading...</div>
    </div>
  </div>
</div>

<script>
(function () {
  'use strict';

  const BASE = '/api/observability/v1';
  const TOKEN_KEY = 'magi-obs:gateway-token';
  const tokenInput = document.getElementById('obs-token');
  const metaDot = document.getElementById('meta-dot');
  const metaText = document.getElementById('meta-text');
  const errorBanner = document.getElementById('error-banner');

  // Restore saved token
  const savedToken = localStorage.getItem(TOKEN_KEY);
  if (savedToken) tokenInput.value = savedToken;
  tokenInput.addEventListener('change', function () {
    localStorage.setItem(TOKEN_KEY, tokenInput.value);
  });

  function getToken() {
    return tokenInput.value.trim();
  }

  function showError(msg) {
    errorBanner.style.display = 'block';
    errorBanner.textContent = '';
    errorBanner.appendChild(document.createTextNode(msg));
  }

  function hideError() {
    errorBanner.style.display = 'none';
  }

  async function api(path) {
    const token = getToken();
    const resp = await fetch(BASE + path, {
      headers: { Authorization: 'Bearer ' + token }
    });
    if (!resp.ok) {
      const text = await resp.text();
      const errMsg = 'API error ' + resp.status + ': ' + path + ' — ' + text.slice(0, 200);
      showError(errMsg);
      throw new Error(errMsg);
    }
    hideError();
    return resp.json();
  }

  // ── Meta line ────────────────────────────────────────────────────────────────

  async function loadMeta() {
    try {
      const data = await api('/meta');
      metaDot.className = 'dot green';
      const parts = [];
      if (data.version) parts.push('v' + data.version);
      if (data.bot_id) parts.push('bot: ' + data.bot_id);
      if (data.events !== undefined) parts.push(data.events + ' events');
      const span = document.createElement('span');
      span.textContent = parts.length ? parts.join('  ·  ') : 'observability online';
      metaText.textContent = '';
      metaText.appendChild(span);
    } catch (_) {
      metaDot.className = 'dot red';
      metaText.textContent = 'Could not load meta';
    }
  }

  // ── Tab routing ──────────────────────────────────────────────────────────────

  const tabHandlers = {};
  const tabPanels = {};
  const tabBtns = {};

  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    const id = btn.dataset.tab;
    tabBtns[id] = btn;
    tabPanels[id] = document.getElementById('tab-' + id);
    btn.addEventListener('click', function () {
      activateTab(id);
    });
  });

  function activateTab(id) {
    Object.keys(tabBtns).forEach(function (k) {
      tabBtns[k].classList.toggle('active', k === id);
      tabPanels[k].classList.toggle('active', k === id);
    });
    if (tabHandlers[id]) tabHandlers[id]();
  }

  // ── Utilities ────────────────────────────────────────────────────────────────

  function safeText(val) {
    return val == null ? '' : String(val);
  }

  function formatTime(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (_) {
      return safeText(ts).slice(0, 8);
    }
  }

  function buildEventRow(ev) {
    const row = document.createElement('div');
    row.className = 'event-row';

    const timeCell = document.createElement('span');
    timeCell.className = 'event-time';
    timeCell.textContent = formatTime(ev.ts);

    const kindCell = document.createElement('span');
    kindCell.className = 'event-kind';
    kindCell.textContent = safeText(ev.kind);

    const toolCell = document.createElement('span');
    toolCell.className = 'event-tool';
    toolCell.textContent = safeText(ev.tool_name);

    const statusCell = document.createElement('span');
    statusCell.className = 'event-status';
    statusCell.textContent = safeText(ev.status);

    const summaryCell = document.createElement('span');
    summaryCell.className = 'event-summary';
    summaryCell.textContent = safeText(ev.summary);

    row.appendChild(timeCell);
    row.appendChild(kindCell);
    row.appendChild(toolCell);
    row.appendChild(statusCell);
    row.appendChild(summaryCell);
    return row;
  }

  // ── Live tab ─────────────────────────────────────────────────────────────────

  const eventList = document.getElementById('event-list');
  const eventListEmpty = document.getElementById('event-list-empty');
  const togglePollBtn = document.getElementById('toggle-poll');
  const clearEventsBtn = document.getElementById('clear-events');

  let polling = true;
  let lastSeenId = 0;
  let pollTimer = null;

  async function pollActivity() {
    if (!polling) return;
    try {
      const data = await api('/activity?limit=100&since_id=' + lastSeenId);
      const events = Array.isArray(data) ? data : (data.events || data.items || []);
      if (events.length > 0) {
        if (eventListEmpty) eventListEmpty.remove();
        events.forEach(function (ev) {
          const id = ev.id;
          if (id !== undefined && id !== null && Number(id) > lastSeenId) {
            lastSeenId = Number(id);
          }
          eventList.appendChild(buildEventRow(ev));
        });
        eventList.scrollTop = eventList.scrollHeight;
      }
    } catch (_) { /* error already shown */ }
    if (polling) pollTimer = setTimeout(pollActivity, 2000);
  }

  togglePollBtn.addEventListener('click', function () {
    polling = !polling;
    togglePollBtn.textContent = polling ? 'Stop' : 'Start';
    togglePollBtn.className = polling ? 'btn primary' : 'btn';
    if (polling) pollActivity();
    else if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  });

  clearEventsBtn.addEventListener('click', function () {
    eventList.textContent = '';
    lastSeenId = 0;
    const empty = document.createElement('div');
    empty.className = 'event-empty';
    empty.id = 'event-list-empty';
    empty.textContent = 'Cleared. Waiting for new events...';
    eventList.appendChild(empty);
  });

  // Start polling immediately
  pollActivity();

  // ── Sessions tab ─────────────────────────────────────────────────────────────

  const sessionsTbody = document.getElementById('sessions-tbody');
  const sessionDetail = document.getElementById('session-detail');
  const sessionDetailTitle = document.getElementById('session-detail-title');
  const sessionEventsContainer = document.getElementById('session-events');

  let sessionsLoaded = false;

  tabHandlers['sessions'] = async function () {
    if (sessionsLoaded) return;
    sessionsLoaded = true;
    try {
      const data = await api('/sessions');
      const sessions = Array.isArray(data) ? data : (data.sessions || []);
      sessionsTbody.textContent = '';
      if (sessions.length === 0) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 4;
        td.style.color = 'var(--soft)';
        td.style.textAlign = 'center';
        td.style.padding = '20px';
        td.textContent = 'No sessions found.';
        tr.appendChild(td);
        sessionsTbody.appendChild(tr);
        return;
      }
      sessions.forEach(function (s) {
        const tr = document.createElement('tr');
        tr.className = 'clickable';

        const tdId = document.createElement('td');
        tdId.style.fontFamily = 'ui-monospace, Menlo, monospace';
        tdId.style.fontSize = '12px';
        tdId.textContent = safeText(s.id || s.session_id);

        const tdEvents = document.createElement('td');
        tdEvents.textContent = safeText(s.event_count);

        const tdTools = document.createElement('td');
        tdTools.textContent = safeText(s.tool_count);

        const tdLast = document.createElement('td');
        tdLast.textContent = formatTime(s.last_active || s.updated_at);

        tr.appendChild(tdId);
        tr.appendChild(tdEvents);
        tr.appendChild(tdTools);
        tr.appendChild(tdLast);

        const sessionId = safeText(s.id || s.session_id);
        tr.addEventListener('click', function () {
          loadSessionEvents(sessionId);
        });

        sessionsTbody.appendChild(tr);
      });
    } catch (_) {
      sessionsTbody.textContent = '';
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.style.color = 'var(--red)';
      td.style.padding = '12px';
      td.textContent = 'Failed to load sessions.';
      tr.appendChild(td);
      sessionsTbody.appendChild(tr);
    }
  };

  async function loadSessionEvents(sessionId) {
    sessionDetail.style.display = 'block';
    sessionDetailTitle.textContent = '';
    const titleNode = document.createTextNode('Events for session: ' + sessionId);
    sessionDetailTitle.appendChild(titleNode);
    sessionEventsContainer.textContent = '';

    const loadingDiv = document.createElement('div');
    loadingDiv.style.color = 'var(--soft)';
    loadingDiv.style.padding = '12px';
    loadingDiv.textContent = 'Loading...';
    sessionEventsContainer.appendChild(loadingDiv);

    try {
      const encodedId = encodeURIComponent(sessionId);
      const data = await api('/sessions/' + encodedId + '/events');
      const events = Array.isArray(data) ? data : (data.events || []);
      sessionEventsContainer.textContent = '';
      if (events.length === 0) {
        const empty = document.createElement('div');
        empty.style.color = 'var(--soft)';
        empty.style.padding = '12px';
        empty.textContent = 'No events in this session.';
        sessionEventsContainer.appendChild(empty);
        return;
      }
      events.forEach(function (ev) {
        sessionEventsContainer.appendChild(buildEventRow(ev));
      });
    } catch (_) {
      sessionEventsContainer.textContent = '';
      const errDiv = document.createElement('div');
      errDiv.style.color = 'var(--red)';
      errDiv.style.padding = '12px';
      errDiv.textContent = 'Failed to load events for session: ' + sessionId;
      sessionEventsContainer.appendChild(errDiv);
    }
  }

  // ── Health tab ───────────────────────────────────────────────────────────────

  const healthPre = document.getElementById('health-pre');
  let healthLoaded = false;

  tabHandlers['health'] = async function () {
    if (healthLoaded) return;
    healthLoaded = true;
    try {
      const data = await api('/health/live');
      healthPre.textContent = JSON.stringify(data, null, 2);
    } catch (_) {
      healthPre.textContent = 'Failed to load health data.';
    }
  };

  // ── Board tab ────────────────────────────────────────────────────────────────

  const boardContent = document.getElementById('board-content');
  let boardLoaded = false;

  tabHandlers['board'] = async function () {
    if (boardLoaded) return;
    boardLoaded = true;
    try {
      const data = await api('/board');
      const board = data && (data.board !== undefined ? data.board : data);
      if (board === null || board === undefined || (typeof board === 'object' && Object.keys(board).length === 0)) {
        boardContent.textContent = 'no board data';
      } else {
        boardContent.textContent = JSON.stringify(board, null, 2);
      }
    } catch (_) {
      boardContent.textContent = 'Failed to load board data.';
    }
  };

  // ── Boot ─────────────────────────────────────────────────────────────────────

  loadMeta();
})();
</script>
</body>
</html>"""


def build_page_router(runtime: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/observability", response_class=HTMLResponse)
    async def observability_page() -> str:
        return _PAGE_HTML

    return router
