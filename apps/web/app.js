const storage = {
  agentUrl: "magi.agent.app.agentUrl",
  token: "magi.agent.app.token",
  sessionKey: "magi.agent.app.sessionKey",
  modelOverride: "magi.agent.app.modelOverride",
};

const state = {
  eventCount: 0,
  streamingMessage: null,
  deferredInstallPrompt: null,
};

const els = {
  connectionForm: document.querySelector("#connection-form"),
  agentUrl: document.querySelector("#agent-url"),
  token: document.querySelector("#server-token"),
  sessionKey: document.querySelector("#session-key"),
  modelOverride: document.querySelector("#model-override"),
  planMode: document.querySelector("#plan-mode"),
  healthButton: document.querySelector("#health-button"),
  installButton: document.querySelector("#install-button"),
  runtimeStatus: document.querySelector("#runtime-status"),
  runtimeSessions: document.querySelector("#runtime-sessions"),
  runtimeTasks: document.querySelector("#runtime-tasks"),
  runtimeCrons: document.querySelector("#runtime-crons"),
  runtimeArtifacts: document.querySelector("#runtime-artifacts"),
  runtimeTools: document.querySelector("#runtime-tools"),
  runtimeSkills: document.querySelector("#runtime-skills"),
  eventCount: document.querySelector("#event-count"),
  sessionLabel: document.querySelector("#session-label"),
  messages: document.querySelector("#messages"),
  events: document.querySelector("#events"),
  sessionsList: document.querySelector("#sessions-list"),
  tasksList: document.querySelector("#tasks-list"),
  cronsList: document.querySelector("#crons-list"),
  artifactsList: document.querySelector("#artifacts-list"),
  toolsList: document.querySelector("#tools-list"),
  skillsList: document.querySelector("#skills-list"),
  messageForm: document.querySelector("#message-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  clearButton: document.querySelector("#clear-button"),
};

function defaultSessionKey() {
  return "agent:local:app:web:default";
}

function loadSettings() {
  els.agentUrl.value = localStorage.getItem(storage.agentUrl) || window.location.origin;
  els.token.value = localStorage.getItem(storage.token) || "";
  els.sessionKey.value = localStorage.getItem(storage.sessionKey) || defaultSessionKey();
  els.modelOverride.value = localStorage.getItem(storage.modelOverride) || "auto";
  updateSessionLabel();
}

function saveSettings() {
  localStorage.setItem(storage.agentUrl, normalizeAgentUrl(els.agentUrl.value));
  localStorage.setItem(storage.token, els.token.value.trim());
  localStorage.setItem(storage.sessionKey, els.sessionKey.value.trim() || defaultSessionKey());
  localStorage.setItem(storage.modelOverride, els.modelOverride.value.trim() || "auto");
  loadSettings();
  addEvent("connection_saved", {
    agentUrl: els.agentUrl.value,
    sessionKey: els.sessionKey.value,
    modelOverride: els.modelOverride.value,
    tokenPresent: els.token.value.trim().length > 0,
  });
}

function normalizeAgentUrl(value) {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed.replace(/\/+$/, "") : window.location.origin;
}

function updateSessionLabel() {
  els.sessionLabel.textContent = els.sessionKey.value.trim() || defaultSessionKey();
}

function headers() {
  const token = els.token.value.trim();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    "X-Core-Agent-Session-Key": els.sessionKey.value.trim() || defaultSessionKey(),
    ...(els.planMode.checked ? { "X-Core-Agent-Plan-Mode": "on" } : {}),
  };
}

function authHeaders() {
  const token = els.token.value.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function addMessage(role, text, extraClass = "") {
  const node = document.createElement("div");
  node.className = `message ${role} ${extraClass}`.trim();
  node.textContent = text;
  els.messages.appendChild(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function appendAssistantText(text) {
  if (!state.streamingMessage) {
    state.streamingMessage = addMessage("assistant", "", "streaming");
  }
  state.streamingMessage.textContent += text;
  state.streamingMessage.scrollIntoView({ block: "end" });
}

function finishAssistantMessage() {
  if (!state.streamingMessage) return;
  state.streamingMessage.classList.remove("streaming");
  state.streamingMessage = null;
}

function addEvent(type, payload) {
  state.eventCount += 1;
  els.eventCount.textContent = String(state.eventCount);
  const node = document.createElement("div");
  node.className = "event";
  const title = document.createElement("strong");
  title.textContent = type;
  const body = document.createElement("span");
  body.textContent = JSON.stringify(payload, null, 2);
  node.append(title, body);
  els.events.prepend(node);
}

async function getJson(path) {
  const base = normalizeAgentUrl(els.agentUrl.value);
  const response = await fetch(`${base}${path}`, {
    headers: authHeaders(),
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    /* keep empty payload */
  }
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function formatTime(ms) {
  if (typeof ms !== "number" || ms <= 0) return "";
  return new Date(ms).toLocaleString();
}

function renderSnapshotList(target, items, emptyText, renderItem) {
  target.textContent = "";
  if (!Array.isArray(items) || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "snapshot-empty";
    empty.textContent = emptyText;
    target.appendChild(empty);
    return;
  }
  for (const item of items) {
    const node = document.createElement("div");
    node.className = "snapshot-item";
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const detail = document.createElement("small");
    const rendered = renderItem(item);
    title.textContent = rendered.title;
    meta.textContent = rendered.meta;
    detail.textContent = rendered.detail;
    node.append(title, meta, detail);
    target.appendChild(node);
  }
}

function renderRuntimeSnapshot(payload) {
  const sessions = payload.sessions?.items || [];
  const tasks = payload.tasks?.items || [];
  const crons = payload.crons?.items || [];
  const artifacts = payload.artifacts?.items || [];
  const tools = payload.tools?.items || [];
  const skills = payload.skills?.loaded || [];

  els.runtimeSessions.textContent = String(payload.sessions?.count || 0);
  els.runtimeTasks.textContent = String(payload.tasks?.count || 0);
  els.runtimeCrons.textContent = String(payload.crons?.count || 0);
  els.runtimeArtifacts.textContent = String(payload.artifacts?.count || 0);
  els.runtimeTools.textContent = String(payload.tools?.count || 0);
  els.runtimeSkills.textContent = String(payload.skills?.loadedCount || 0);

  renderSnapshotList(els.sessionsList, sessions, "No live sessions", (session) => ({
    title: session.sessionKey || "session",
    meta: `${session.permissionMode || "default"} - ${session.channel?.type || "channel"}`,
    detail: `${session.budget?.turns || 0} turns - last ${formatTime(session.lastActivityAt)}`,
  }));
  renderSnapshotList(els.tasksList, tasks, "No background tasks", (task) => ({
    title: task.taskId || "task",
    meta: `${task.status || "unknown"} - ${task.persona || "agent"}`,
    detail: task.promptPreview || task.resultPreview || "No preview",
  }));
  renderSnapshotList(els.cronsList, crons, "No scheduled jobs", (cron) => ({
    title: cron.cronId || "cron",
    meta: `${cron.enabled ? "enabled" : "disabled"} - ${cron.expression || "schedule"}`,
    detail: `${cron.internal ? "internal" : cron.durable ? "durable" : "session"} - next ${formatTime(
      cron.nextFireAt,
    )}`,
  }));
  renderSnapshotList(els.artifactsList, artifacts, "No artifacts", (artifact) => ({
    title: artifact.title || artifact.artifactId || "artifact",
    meta: `${artifact.kind || "artifact"} - ${artifact.sizeBytes || 0} bytes`,
    detail: artifact.path || artifact.slug || "",
  }));
  renderSnapshotList(els.toolsList, tools, "No tools registered", (tool) => ({
    title: tool.name || "tool",
    meta: `${tool.permission || "read"} - ${tool.kind || "core"}`,
    detail: tool.dangerous ? "dangerous" : "",
  }));
  renderSnapshotList(els.skillsList, skills, "No loaded skills", (skill) => ({
    title: skill.name || "skill",
    meta: skill.path || "loaded",
    detail:
      payload.skills?.runtimeHookCount > 0
        ? `${payload.skills.runtimeHookCount} runtime hooks active`
        : "",
  }));
}

async function loadRuntimeSnapshot() {
  const payload = await getJson("/v1/app/runtime?limit=12");
  renderRuntimeSnapshot(payload);
  addEvent("runtime_snapshot", {
    sessions: payload.sessions?.count || 0,
    tasks: payload.tasks?.count || 0,
    crons: payload.crons?.count || 0,
    artifacts: payload.artifacts?.count || 0,
    tools: payload.tools?.count || 0,
    skills: payload.skills?.loadedCount || 0,
  });
}

async function checkRuntime() {
  const base = normalizeAgentUrl(els.agentUrl.value);
  els.runtimeStatus.textContent = "Checking";
  try {
    const response = await fetch(`${base}/health`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText);
    els.runtimeStatus.textContent = `${payload.runtime || "runtime"} ${payload.version || ""}`.trim();
    addEvent("health", payload);
    try {
      await loadRuntimeSnapshot();
    } catch (error) {
      addEvent("runtime_snapshot_error", { message: String(error.message || error) });
    }
  } catch (error) {
    els.runtimeStatus.textContent = "Unavailable";
    addEvent("health_error", { message: String(error.message || error) });
  }
}

export function createSseParser(onEvent) {
  let buffer = "";
  return (chunk) => {
    buffer += chunk;
    const frames = buffer.split(/\n\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const lines = frame.split(/\n/);
      let event = "message";
      const data = [];
      for (const line of lines) {
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice("event:".length).trim();
        if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart());
      }
      if (data.length > 0) onEvent(event, data.join("\n"));
    }
  };
}

function handleSseEvent(eventName, rawData) {
  if (rawData === "[DONE]") {
    finishAssistantMessage();
    addEvent("done", {});
    return;
  }
  let payload;
  try {
    payload = JSON.parse(rawData);
  } catch {
    addEvent("sse_parse_error", { eventName, rawData });
    return;
  }

  if (eventName === "agent") {
    addEvent(payload.type || "agent", payload);
    if (payload.type === "text_delta" && typeof payload.delta === "string") {
      appendAssistantText(payload.delta);
    }
    if (payload.type === "turn_end") {
      finishAssistantMessage();
    }
    return;
  }

  const delta = payload.choices?.[0]?.delta?.content;
  if (typeof delta === "string" && delta.length > 0) {
    appendAssistantText(delta);
  }
  if (payload.choices?.[0]?.finish_reason) {
    finishAssistantMessage();
  }
}

async function sendMessage(text) {
  const base = normalizeAgentUrl(els.agentUrl.value);
  const modelOverride = els.modelOverride.value.trim();
  const response = await fetch(`${base}/v1/chat/completions`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      stream: true,
      ...(modelOverride ? { model: modelOverride } : {}),
      messages: [{ role: "user", content: text }],
    }),
  });
  if (!response.ok || !response.body) {
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      /* keep empty payload */
    }
    throw new Error(payload.error || response.statusText);
  }

  const decoder = new TextDecoder();
  const parser = createSseParser(handleSseEvent);
  for await (const chunk of response.body) {
    parser(decoder.decode(chunk, { stream: true }));
  }
  parser(decoder.decode());
  finishAssistantMessage();
}

els.connectionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings();
});

els.healthButton.addEventListener("click", () => {
  void checkRuntime();
});

els.installButton.addEventListener("click", async () => {
  const prompt = state.deferredInstallPrompt;
  if (!prompt) return;
  state.deferredInstallPrompt = null;
  els.installButton.hidden = true;
  prompt.prompt();
  const choice = await prompt.userChoice;
  addEvent("install_prompt", { outcome: choice?.outcome || "unknown" });
});

els.sessionKey.addEventListener("input", updateSessionLabel);

els.clearButton.addEventListener("click", () => {
  els.messages.textContent = "";
  els.events.textContent = "";
  state.eventCount = 0;
  state.streamingMessage = null;
  els.eventCount.textContent = "0";
});

els.messageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.messageInput.value.trim();
  if (text.length === 0) return;
  saveSettings();
  addMessage("user", text);
  els.messageInput.value = "";
  els.sendButton.disabled = true;
  try {
    await sendMessage(text);
    await loadRuntimeSnapshot();
  } catch (error) {
    finishAssistantMessage();
    addMessage("assistant", String(error.message || error), "error");
    addEvent("send_error", { message: String(error.message || error) });
  } finally {
    els.sendButton.disabled = false;
    els.messageInput.focus();
  }
});

loadSettings();
addEvent("app_ready", {
  agentUrl: els.agentUrl.value,
  sessionKey: els.sessionKey.value,
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.deferredInstallPrompt = event;
  els.installButton.hidden = false;
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/app/sw.js", { scope: "/app/" })
    .then(() => addEvent("service_worker_ready", {}))
    .catch((error) =>
      addEvent("service_worker_error", { message: String(error.message || error) }),
    );
}
