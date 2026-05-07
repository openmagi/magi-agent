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
  reloadSkillsButton: document.querySelector("#reload-skills-button"),
  workspaceForm: document.querySelector("#workspace-form"),
  workspacePath: document.querySelector("#workspace-path"),
  workspaceList: document.querySelector("#workspace-list"),
  workspaceFile: document.querySelector("#workspace-file"),
  memorySearchForm: document.querySelector("#memory-search-form"),
  memorySearchQuery: document.querySelector("#memory-search-query"),
  loadMemoryButton: document.querySelector("#load-memory-button"),
  memoryList: document.querySelector("#memory-list"),
  memoryResults: document.querySelector("#memory-results"),
  memoryFile: document.querySelector("#memory-file"),
  taskControlForm: document.querySelector("#task-control-form"),
  taskControlId: document.querySelector("#task-control-id"),
  taskOutputButton: document.querySelector("#task-output-button"),
  taskStopButton: document.querySelector("#task-stop-button"),
  taskOutputView: document.querySelector("#task-output-view"),
  cronEditorForm: document.querySelector("#cron-editor-form"),
  cronId: document.querySelector("#cron-id"),
  cronExpression: document.querySelector("#cron-expression"),
  cronPrompt: document.querySelector("#cron-prompt"),
  cronDescription: document.querySelector("#cron-description"),
  cronEnabled: document.querySelector("#cron-enabled"),
  cronDurable: document.querySelector("#cron-durable"),
  deleteCronButton: document.querySelector("#delete-cron-button"),
  runtimeConfigForm: document.querySelector("#runtime-config-form"),
  configProvider: document.querySelector("#config-provider"),
  configModel: document.querySelector("#config-model"),
  configBaseUrl: document.querySelector("#config-base-url"),
  configApiKeyEnv: document.querySelector("#config-api-key-env"),
  configServerTokenEnv: document.querySelector("#config-server-token-env"),
  configContextWindow: document.querySelector("#config-context-window"),
  configMaxOutput: document.querySelector("#config-max-output"),
  loadConfigButton: document.querySelector("#load-config-button"),
  harnessRuleForm: document.querySelector("#harness-rule-form"),
  harnessRuleName: document.querySelector("#harness-rule-name"),
  harnessRuleContent: document.querySelector("#harness-rule-content"),
  deleteRuleButton: document.querySelector("#delete-rule-button"),
  harnessRulesList: document.querySelector("#harness-rules-list"),
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

async function sendJson(path, method, body) {
  const base = normalizeAgentUrl(els.agentUrl.value);
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
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

function renderSnapshotList(target, items, emptyText, renderItem, onSelect) {
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
    if (onSelect) {
      node.classList.add("clickable");
      node.tabIndex = 0;
      node.addEventListener("click", () => onSelect(item));
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(item);
        }
      });
    }
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
  }), (task) => {
    els.taskControlId.value = task.taskId || "";
    void loadTaskOutput().catch((error) =>
      addEvent("task_output_error", { message: String(error.message || error) }),
    );
  });
  renderSnapshotList(els.cronsList, crons, "No scheduled jobs", (cron) => ({
    title: cron.cronId || "cron",
    meta: `${cron.enabled ? "enabled" : "disabled"} - ${cron.expression || "schedule"}`,
    detail: `${cron.internal ? "internal" : cron.durable ? "durable" : "session"} - next ${formatTime(
      cron.nextFireAt,
    )}`,
  }), (cron) => fillCronForm(cron));
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

function renderWorkspace(entries) {
  renderSnapshotList(
    els.workspaceList,
    entries,
    "No files",
    (entry) => ({
      title: entry.name || entry.path || "entry",
      meta: `${entry.type || "entry"} - ${entry.sizeBytes || 0} bytes`,
      detail: entry.path || "",
    }),
    (entry) => {
      if (entry.type === "directory") {
        void loadWorkspace(entry.path).catch((error) =>
          addEvent("workspace_error", { message: String(error.message || error) }),
        );
        return;
      }
      if (entry.type === "file") {
        void loadWorkspaceFile(entry.path).catch((error) =>
          addEvent("workspace_error", { message: String(error.message || error) }),
        );
      }
    },
  );
}

async function loadWorkspace(pathValue = els.workspacePath.value.trim() || ".") {
  const payload = await getJson(
    `/v1/app/workspace?path=${encodeURIComponent(pathValue)}`,
  );
  els.workspacePath.value = payload.path || ".";
  renderWorkspace(payload.entries || []);
  addEvent("workspace_loaded", {
    path: payload.path || ".",
    count: Array.isArray(payload.entries) ? payload.entries.length : 0,
  });
}

async function loadWorkspaceFile(pathValue) {
  const payload = await getJson(
    `/v1/app/workspace/file?path=${encodeURIComponent(pathValue)}`,
  );
  els.workspaceFile.textContent = payload.content || "";
  addEvent("workspace_file_loaded", {
    path: payload.path,
    sizeBytes: payload.sizeBytes,
    truncated: payload.truncated === true,
  });
}

function renderMemoryFiles(files) {
  renderSnapshotList(
    els.memoryList,
    files,
    "No memory files",
    (file) => ({
      title: file.path || "memory",
      meta: `${file.sizeBytes || 0} bytes`,
      detail: formatTime(file.mtimeMs),
    }),
    (file) => {
      void loadMemoryFile(file.path).catch((error) =>
        addEvent("memory_error", { message: String(error.message || error) }),
      );
    },
  );
}

async function loadMemory() {
  const payload = await getJson("/v1/app/memory");
  renderMemoryFiles(payload.files || []);
  addEvent("memory_loaded", {
    count: Array.isArray(payload.files) ? payload.files.length : 0,
    qmdReady: payload.status?.qmdReady === true,
  });
}

async function loadMemoryFile(pathValue) {
  const payload = await getJson(
    `/v1/app/memory/file?path=${encodeURIComponent(pathValue)}`,
  );
  els.memoryFile.textContent = payload.content || "";
  addEvent("memory_file_loaded", {
    path: payload.path,
    sizeBytes: payload.sizeBytes,
    truncated: payload.truncated === true,
  });
}

async function searchMemory() {
  const query = els.memorySearchQuery.value.trim();
  if (!query) throw new Error("Memory search query is required");
  const payload = await getJson(
    `/v1/app/memory/search?q=${encodeURIComponent(query)}&limit=8`,
  );
  renderSnapshotList(
    els.memoryResults,
    payload.results || [],
    "No search results",
    (result) => ({
      title: result.path || "result",
      meta: `score ${typeof result.score === "number" ? result.score.toFixed(2) : "0.00"}`,
      detail: result.contentPreview || result.context || "",
    }),
    (result) => {
      void loadMemoryFile(result.path).catch((error) =>
        addEvent("memory_error", { message: String(error.message || error) }),
      );
    },
  );
  addEvent("memory_search", {
    query: payload.query,
    count: Array.isArray(payload.results) ? payload.results.length : 0,
  });
}

function fillCronForm(cron) {
  els.cronId.value = cron.cronId || "";
  els.cronExpression.value = cron.expression || "";
  els.cronPrompt.value = cron.promptPreview || "";
  els.cronDescription.value = cron.description || "";
  els.cronEnabled.checked = cron.enabled !== false;
  els.cronDurable.checked = cron.durable === true;
}

async function loadTaskOutput() {
  const taskId = els.taskControlId.value.trim();
  if (!taskId) throw new Error("Task ID is required");
  const payload = await getJson(
    `/v1/app/tasks/${encodeURIComponent(taskId)}/output`,
  );
  els.taskOutputView.textContent = JSON.stringify(payload, null, 2);
  addEvent("task_output_loaded", {
    taskId: payload.taskId,
    status: payload.status,
  });
}

async function stopTask() {
  const taskId = els.taskControlId.value.trim();
  if (!taskId) throw new Error("Task ID is required");
  const payload = await sendJson(
    `/v1/app/tasks/${encodeURIComponent(taskId)}/stop`,
    "POST",
    { reason: "stopped from Magi App" },
  );
  els.taskOutputView.textContent = JSON.stringify(payload, null, 2);
  await loadRuntimeSnapshot();
  addEvent("task_stopped", {
    taskId: payload.taskId,
    stopped: payload.stopped === true,
  });
}

async function saveCron() {
  const cronId = els.cronId.value.trim();
  const body = {
    expression: els.cronExpression.value.trim(),
    prompt: els.cronPrompt.value,
    description: els.cronDescription.value.trim(),
    sessionKey: els.sessionKey.value.trim() || defaultSessionKey(),
    durable: els.cronDurable.checked,
    enabled: els.cronEnabled.checked,
  };
  if (!body.expression || !body.prompt.trim()) {
    throw new Error("Cron expression and prompt are required");
  }
  const payload = cronId
    ? await sendJson(`/v1/app/crons/${encodeURIComponent(cronId)}`, "PUT", body)
    : await sendJson("/v1/app/crons", "POST", body);
  fillCronForm(payload.cron || {});
  await loadRuntimeSnapshot();
  addEvent("cron_saved", { cronId: payload.cron?.cronId || cronId });
}

async function deleteCron() {
  const cronId = els.cronId.value.trim();
  if (!cronId) throw new Error("Cron ID is required");
  const payload = await sendJson(
    `/v1/app/crons/${encodeURIComponent(cronId)}`,
    "DELETE",
    {},
  );
  els.cronId.value = "";
  els.cronExpression.value = "";
  els.cronPrompt.value = "";
  els.cronDescription.value = "";
  await loadRuntimeSnapshot();
  addEvent("cron_deleted", {
    cronId: payload.cronId || cronId,
    deleted: payload.deleted === true,
  });
}

async function reloadSkills() {
  const payload = await sendJson("/v1/app/skills/reload", "POST", {});
  await loadRuntimeSnapshot();
  addEvent("skills_reloaded", {
    loaded: Array.isArray(payload.loaded) ? payload.loaded.length : 0,
    issues: Array.isArray(payload.issues) ? payload.issues.length : 0,
  });
}

function numericValue(input) {
  const raw = input.value.trim();
  if (!raw) return undefined;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

async function loadAppConfig() {
  const payload = await getJson("/v1/app/config");
  const config = payload.config || {};
  const llm = config.llm || {};
  const server = config.server || {};
  els.configProvider.value = llm.provider || "openai-compatible";
  els.configModel.value = llm.model || "llama3.1";
  els.configBaseUrl.value = llm.baseUrl || "";
  els.configApiKeyEnv.value = llm.apiKeyEnvVar || "";
  els.configServerTokenEnv.value = server.gatewayTokenEnvVar || "MAGI_AGENT_SERVER_TOKEN";
  els.configContextWindow.value = llm.capabilities?.contextWindow || "";
  els.configMaxOutput.value = llm.capabilities?.maxOutputTokens || "";
  addEvent("config_loaded", {
    exists: payload.exists === true,
    provider: llm.provider,
    model: llm.model,
    apiKeySet: llm.apiKeySet === true,
  });
}

async function saveAppConfig() {
  const contextWindow = numericValue(els.configContextWindow);
  const maxOutputTokens = numericValue(els.configMaxOutput);
  const capabilities =
    contextWindow || maxOutputTokens
      ? {
          ...(contextWindow ? { contextWindow } : {}),
          ...(maxOutputTokens ? { maxOutputTokens } : {}),
          supportsThinking: false,
          inputUsdPerMtok: 0,
          outputUsdPerMtok: 0,
        }
      : undefined;
  await sendJson("/v1/app/config", "PUT", {
    llm: {
      provider: els.configProvider.value,
      model: els.configModel.value.trim() || "llama3.1",
      baseUrl: els.configBaseUrl.value.trim(),
      apiKeyEnvVar: els.configApiKeyEnv.value.trim(),
      capabilities,
    },
    server: {
      gatewayTokenEnvVar: els.configServerTokenEnv.value.trim() || "MAGI_AGENT_SERVER_TOKEN",
    },
    workspace: "./workspace",
  });
  addEvent("config_saved", {
    provider: els.configProvider.value,
    model: els.configModel.value.trim() || "llama3.1",
  });
}

function renderHarnessRules(rules) {
  els.harnessRulesList.textContent = "";
  if (!Array.isArray(rules) || rules.length === 0) {
    const empty = document.createElement("div");
    empty.className = "snapshot-empty";
    empty.textContent = "No harness rules";
    els.harnessRulesList.appendChild(empty);
    return;
  }
  for (const rule of rules) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rule-button";
    button.textContent = `${rule.name} (${rule.sizeBytes || 0} bytes)`;
    button.addEventListener("click", () => {
      void loadHarnessRule(rule.name);
    });
    els.harnessRulesList.appendChild(button);
  }
}

async function loadHarnessRules() {
  const payload = await getJson("/v1/app/harness-rules");
  renderHarnessRules(payload.rules || []);
  addEvent("harness_rules_loaded", { count: Array.isArray(payload.rules) ? payload.rules.length : 0 });
}

async function loadHarnessRule(name) {
  const payload = await getJson(`/v1/app/harness-rules/${encodeURIComponent(name)}`);
  els.harnessRuleName.value = payload.name || name;
  els.harnessRuleContent.value = payload.content || "";
  addEvent("harness_rule_loaded", { name: payload.name || name });
}

async function saveHarnessRule() {
  const name = els.harnessRuleName.value.trim();
  if (!name) throw new Error("Rule file name is required");
  await sendJson(`/v1/app/harness-rules/${encodeURIComponent(name)}`, "PUT", {
    content: els.harnessRuleContent.value,
  });
  await loadHarnessRules();
  addEvent("harness_rule_saved", { name });
}

async function deleteHarnessRule() {
  const name = els.harnessRuleName.value.trim();
  if (!name) throw new Error("Rule file name is required");
  await sendJson(`/v1/app/harness-rules/${encodeURIComponent(name)}`, "DELETE", {});
  els.harnessRuleContent.value = "";
  await loadHarnessRules();
  addEvent("harness_rule_deleted", { name });
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
      await loadAppConfig();
      await loadHarnessRules();
      await loadWorkspace();
      await loadMemory();
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

els.loadConfigButton.addEventListener("click", () => {
  void loadAppConfig().catch((error) =>
    addEvent("config_error", { message: String(error.message || error) }),
  );
});

els.reloadSkillsButton.addEventListener("click", () => {
  void reloadSkills().catch((error) =>
    addEvent("skills_reload_error", { message: String(error.message || error) }),
  );
});

els.workspaceForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void loadWorkspace().catch((error) =>
    addEvent("workspace_error", { message: String(error.message || error) }),
  );
});

els.memorySearchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void searchMemory().catch((error) =>
    addEvent("memory_error", { message: String(error.message || error) }),
  );
});

els.loadMemoryButton.addEventListener("click", () => {
  void loadMemory().catch((error) =>
    addEvent("memory_error", { message: String(error.message || error) }),
  );
});

els.taskControlForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void loadTaskOutput().catch((error) =>
    addEvent("task_output_error", { message: String(error.message || error) }),
  );
});

els.taskOutputButton.addEventListener("click", () => {
  void loadTaskOutput().catch((error) =>
    addEvent("task_output_error", { message: String(error.message || error) }),
  );
});

els.taskStopButton.addEventListener("click", () => {
  void stopTask().catch((error) =>
    addEvent("task_stop_error", { message: String(error.message || error) }),
  );
});

els.cronEditorForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveCron().catch((error) =>
    addEvent("cron_error", { message: String(error.message || error) }),
  );
});

els.deleteCronButton.addEventListener("click", () => {
  void deleteCron().catch((error) =>
    addEvent("cron_error", { message: String(error.message || error) }),
  );
});

els.runtimeConfigForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveAppConfig().catch((error) =>
    addEvent("config_error", { message: String(error.message || error) }),
  );
});

els.harnessRuleForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveHarnessRule().catch((error) =>
    addEvent("harness_rule_error", { message: String(error.message || error) }),
  );
});

els.deleteRuleButton.addEventListener("click", () => {
  void deleteHarnessRule().catch((error) =>
    addEvent("harness_rule_error", { message: String(error.message || error) }),
  );
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
