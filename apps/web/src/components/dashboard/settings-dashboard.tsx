import { useState, useEffect, useCallback } from "react";
import {
  DashboardPageHeader,
  DashboardCard,
  StatusPill,
  EmptyState,
  ButtonLike,
  SettingsInput,
  SettingsDropdown,
  CollapsibleCard,
  runtimeStatusLabel,
  type JsonRecord,
  type RuntimeCheckStatus,
} from "./shared";
import { ToolsSettings } from "./tools-settings";
import { HooksSettings } from "./hooks-settings";
import { ClassifierSettings } from "./classifier-settings";
import { CustomizeTab } from "./customize/customize-tab";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type ProviderName =
  | "anthropic"
  | "openai"
  | "google"
  | "openai-compatible";

export interface LocalConfigState {
  path: string;
  exists: boolean;
  provider: ProviderName;
  model: string;
  baseUrl: string;
  apiKeyEnvVar: string;
  gatewayTokenEnvVar: string;
  workspace: string;
  contextWindow: string;
  maxOutputTokens: string;
  supportsThinking: boolean;
  restartRequired: boolean;
  liveReloadSupported: boolean;
}

function asProviderName(value: unknown): ProviderName {
  return value === "anthropic" ||
    value === "openai" ||
    value === "google" ||
    value === "openai-compatible"
    ? value
    : "openai-compatible";
}

/* ------------------------------------------------------------------ */
/*  SettingsDashboard                                                  */
/* ------------------------------------------------------------------ */

type SettingsTab = "config" | "customize" | "tools" | "hooks" | "classifier";

export interface SettingsDashboardProps {
  agentUrl: string;
  token: string;
  runtimeStatus: RuntimeCheckStatus;
  config: LocalConfigState | null;
  configLoading: boolean;
  configSaving: boolean;
  configNotice: string | null;
  configError: string | null;
  setAgentUrl: (value: string) => void;
  setToken: (value: string) => void;
  onSaveConnection: () => void;
  onCheckRuntime: () => void;
  onSaveConfig: (config: LocalConfigState) => Promise<void>;
  onReloadConfig: () => Promise<void>;
  onRestartRuntime: () => Promise<void>;
  getJson: (path: string) => Promise<Record<string, unknown>>;
  sendJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
  putJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
  deleteJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
}

export function SettingsDashboard({
  agentUrl,
  token,
  runtimeStatus,
  config,
  configLoading,
  configSaving,
  configNotice,
  configError,
  setAgentUrl,
  setToken,
  onSaveConnection,
  onCheckRuntime,
  onSaveConfig,
  onReloadConfig,
  onRestartRuntime,
  getJson,
  sendJson,
  putJson,
  deleteJson,
}: SettingsDashboardProps) {
  const [draft, setDraft] = useState<LocalConfigState | null>(config);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("config");

  useEffect(() => {
    setDraft(config);
  }, [config]);

  const updateDraft = useCallback((patch: Partial<LocalConfigState>) => {
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const settingsTabs: Array<{ key: SettingsTab; label: string }> = [
    { key: "config", label: "Configuration" },
    { key: "customize", label: "Customize" },
    { key: "tools", label: "Tools" },
    { key: "hooks", label: "Hooks" },
    { key: "classifier", label: "Classifier" },
  ];

  const tabEyebrow: Record<SettingsTab, string> = {
    config: "Configuration",
    customize: "Customize",
    tools: "Tool Registry",
    hooks: "Verification Hooks",
    classifier: "Classifier Dimensions",
  };
  const tabDescription: Record<SettingsTab, string> = {
    config:
      "Configure the local runtime, provider endpoint, workspace path, and safeguards used by the self-hosted agent.",
    customize:
      "Unified safeguard, tool, and hook management. Configure verification rules and custom tools from a single view.",
    tools:
      "View and manage all registered tools. Enable, disable, or remove tools from the runtime.",
    hooks:
      "Inspect and manage hooks that verify every response. Create new hooks from natural language.",
    classifier:
      "Add custom classifier dimensions that run alongside built-in classification on every turn.",
  };

  return (
    <div className="max-w-4xl space-y-5">
      <DashboardPageHeader
        eyebrow={tabEyebrow[settingsTab]}
        title="Settings"
        description={tabDescription[settingsTab]}
        action={
          <StatusPill status={runtimeStatus}>
            {runtimeStatusLabel(runtimeStatus)}
          </StatusPill>
        }
      />

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 pb-px">
        {settingsTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSettingsTab(tab.key)}
            className={`cursor-pointer rounded-t-lg px-4 py-2 text-sm font-medium transition-colors ${
              settingsTab === tab.key
                ? "border-b-2 border-primary bg-white text-foreground"
                : "text-secondary hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {settingsTab === "customize" && (
        <CustomizeTab
          botId="local"
          initialRules={null}
          initialAgentConfig={{}}
        />
      )}
      {settingsTab === "tools" && (
        <ToolsSettings
          getJson={getJson}
          putJson={putJson}
          deleteJson={deleteJson}
        />
      )}
      {settingsTab === "hooks" && (
        <HooksSettings
          getJson={getJson}
          sendJson={sendJson}
          deleteJson={deleteJson}
        />
      )}
      {settingsTab === "classifier" && (
        <ClassifierSettings sendJson={sendJson} />
      )}

      {settingsTab === "config" && (
        <>
          {/* Model card */}
          <DashboardCard
            title="Model"
            action={
              configLoading ? (
                <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-secondary">
                  Loading
                </span>
              ) : null
            }
          >
            {!draft ? (
              <EmptyState>
                No config loaded yet. Save a local provider below to create
                `magi-agent.yaml`.
              </EmptyState>
            ) : (
              <div className="space-y-4">
                {configNotice && (
                  <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">
                    {configNotice}
                  </div>
                )}
                {configError && (
                  <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-500">
                    {configError}
                  </div>
                )}
                <SettingsDropdown
                  label="Provider"
                  value={draft.provider}
                  onChange={(value) =>
                    updateDraft({ provider: asProviderName(value) })
                  }
                  options={[
                    {
                      value: "openai-compatible",
                      label: "OpenAI-compatible / local",
                    },
                    { value: "anthropic", label: "Anthropic" },
                    { value: "openai", label: "OpenAI" },
                    { value: "google", label: "Google Gemini" },
                  ]}
                />
                <SettingsInput
                  label="Model"
                  value={draft.model}
                  onChange={(model) => updateDraft({ model })}
                  placeholder="llama3.1, gpt-4.1, claude-sonnet-4-5..."
                />
                <SettingsInput
                  label="Base URL"
                  value={draft.baseUrl}
                  onChange={(baseUrl) => updateDraft({ baseUrl })}
                  placeholder="http://127.0.0.1:11434/v1"
                />
                <SettingsInput
                  label="API key env var"
                  value={draft.apiKeyEnvVar}
                  onChange={(apiKeyEnvVar) => updateDraft({ apiKeyEnvVar })}
                  placeholder="OPENAI_API_KEY"
                />
                <SettingsDropdown
                  label="Response Language"
                  value="auto"
                  onChange={() => {}}
                  options={[{ value: "auto", label: "Auto Detect" }]}
                />

                <div className="border-t border-gray-200 pt-4">
                  <p className="mb-2 block text-sm font-medium text-secondary">
                    API Key Mode
                  </p>
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-foreground">
                      Local env vars
                    </p>
                    <span className="text-xs text-secondary">
                      No platform credits or hosted routers
                    </span>
                  </div>
                </div>

                <div className="flex flex-wrap gap-3">
                  <ButtonLike
                    onClick={() => void onSaveConfig(draft)}
                    disabled={configSaving}
                  >
                    {configSaving ? "Saving..." : "Save Settings"}
                  </ButtonLike>
                  <ButtonLike
                    variant="secondary"
                    onClick={() => void onReloadConfig()}
                  >
                    Reload Config
                  </ButtonLike>
                  <ButtonLike
                    variant="secondary"
                    onClick={() => void onRestartRuntime()}
                  >
                    Restart Runtime
                  </ButtonLike>
                </div>
                <p className="text-xs leading-5 text-secondary">
                  Secrets are stored as environment variable references in
                  `magi-agent.yaml`; raw keys are never returned to the browser.
                </p>
              </div>
            )}
          </DashboardCard>

          {/* Runtime Connection */}
          <CollapsibleCard
            title="Runtime Connection"
            subtitle={`Current status: ${runtimeStatusLabel(runtimeStatus)}`}
            defaultOpen={false}
          >
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                onSaveConnection();
              }}
            >
              <SettingsInput
                label="Agent URL"
                value={agentUrl}
                onChange={setAgentUrl}
              />
              <SettingsInput
                label="Server token"
                value={token}
                onChange={setToken}
                type="password"
              />
              <div className="flex flex-wrap gap-3">
                <ButtonLike type="submit">Save Settings</ButtonLike>
                <ButtonLike variant="secondary" onClick={onCheckRuntime}>
                  Check Runtime
                </ButtonLike>
              </div>
            </form>
          </CollapsibleCard>

          {/* Advanced Runtime */}
          <CollapsibleCard
            title="Advanced Runtime"
            subtitle={
              draft?.path
                ? `Config path: ${draft.path}`
                : "Workspace and capability metadata"
            }
            defaultOpen={false}
          >
            {draft && (
              <div className="space-y-4">
                <SettingsInput
                  label="Workspace"
                  value={draft.workspace}
                  onChange={(workspace) => updateDraft({ workspace })}
                  placeholder="./workspace"
                />
                <SettingsInput
                  label="Gateway token env var"
                  value={draft.gatewayTokenEnvVar}
                  onChange={(gatewayTokenEnvVar) =>
                    updateDraft({ gatewayTokenEnvVar })
                  }
                  placeholder="MAGI_AGENT_SERVER_TOKEN"
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  <SettingsInput
                    label="Context window"
                    value={draft.contextWindow}
                    onChange={(contextWindow) =>
                      updateDraft({ contextWindow })
                    }
                    placeholder="131072"
                  />
                  <SettingsInput
                    label="Max output tokens"
                    value={draft.maxOutputTokens}
                    onChange={(maxOutputTokens) =>
                      updateDraft({ maxOutputTokens })
                    }
                    placeholder="8192"
                  />
                </div>
                <label className="flex cursor-pointer items-center gap-3 text-sm text-secondary">
                  <input
                    type="checkbox"
                    checked={draft.supportsThinking}
                    onChange={(event) =>
                      updateDraft({
                        supportsThinking: event.target.checked,
                      })
                    }
                    className="h-4 w-4 rounded border-black/10"
                  />
                  Model supports thinking blocks
                </label>
                <ButtonLike
                  onClick={() => void onSaveConfig(draft)}
                  disabled={configSaving}
                >
                  Save Advanced Settings
                </ButtonLike>
              </div>
            )}
          </CollapsibleCard>

          {/* Agent Safeguards */}
          <CollapsibleCard
            title="Agent Safeguards"
            subtitle="Edit local skills, contracts, harness rules, hooks, memory, and compaction files from Workspace."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                <div className="text-sm font-semibold text-foreground">
                  Custom skills
                </div>
                <div className="mt-1 text-xs leading-5 text-secondary">
                  Install reusable SKILL.md-style capabilities on the Skills
                  page.
                </div>
              </div>
              <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3">
                <div className="text-sm font-semibold text-foreground">
                  Harness rules
                </div>
                <div className="mt-1 text-xs leading-5 text-secondary">
                  Markdown rules become runtime checks through the local
                  workspace.
                </div>
              </div>
            </div>
          </CollapsibleCard>
        </>
      )}
    </div>
  );
}
