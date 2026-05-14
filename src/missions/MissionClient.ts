import type {
  CreateMissionInput,
  ListMissionActionEventsInput,
  MissionActionEvent,
  MissionRecord,
  RestartRecoveryInput,
  RestartRecoveryResult,
} from "./types.js";

export interface MissionClientOptions {
  chatProxyUrl: string;
  gatewayToken: string;
  fetchImpl?: typeof fetch;
}

export class MissionClient {
  private readonly baseUrl: string;
  private readonly gatewayToken: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: MissionClientOptions) {
    this.baseUrl = options.chatProxyUrl.replace(/\/$/, "");
    this.gatewayToken = options.gatewayToken;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async createMission(input: CreateMissionInput): Promise<MissionRecord> {
    const response = await this.request("/v1/missions", input);
    const first = Array.isArray(response) ? response[0] : response;
    if (!first || typeof first.id !== "string") {
      throw new Error("mission create returned no id");
    }
    return first as unknown as MissionRecord;
  }

  async createRun(
    missionId: string,
    input: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const response = await this.request(
      `/v1/missions/${encodeURIComponent(missionId)}/runs`,
      input,
    );
    return Array.isArray(response) ? response[0] ?? {} : response;
  }

  async updateRun(
    missionId: string,
    runId: string,
    input: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const response = await this.requestPatch(
      `/v1/missions/${encodeURIComponent(missionId)}/runs/${encodeURIComponent(runId)}`,
      input,
    );
    return Array.isArray(response) ? response[0] ?? {} : response;
  }

  async appendEvent(
    missionId: string,
    input: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const response = await this.request(
      `/v1/missions/${encodeURIComponent(missionId)}/events`,
      input,
    );
    return Array.isArray(response) ? response[0] ?? {} : response;
  }

  async createArtifact(
    missionId: string,
    input: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const response = await this.request(
      `/v1/missions/${encodeURIComponent(missionId)}/artifacts`,
      input,
    );
    return Array.isArray(response) ? response[0] ?? {} : response;
  }

  async listActionEvents(
    input: ListMissionActionEventsInput = {},
  ): Promise<MissionActionEvent[]> {
    const params = new URLSearchParams();
    if (input.since) params.set("since", input.since);
    if (input.limit !== undefined) params.set("limit", String(input.limit));
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await this.requestGet(`/v1/missions/actions${suffix}`);
    const events = Array.isArray(response)
      ? response
      : Array.isArray(response.events)
        ? response.events
        : [];
    return events.filter(isMissionActionEvent);
  }

  async abandonRunningOnRestart(
    input: RestartRecoveryInput,
  ): Promise<RestartRecoveryResult> {
    const response = await this.request("/v1/missions/restart-recovery", input);
    const record = Array.isArray(response) ? response[0] ?? {} : response;
    return {
      abandoned: typeof record.abandoned === "number" ? record.abandoned : 0,
      missionIds: Array.isArray(record.missionIds)
        ? record.missionIds.filter((id): id is string => typeof id === "string")
        : [],
      resumeRequested: typeof record.resumeRequested === "number"
        ? record.resumeRequested
        : 0,
      resumeMissionIds: Array.isArray(record.resumeMissionIds)
        ? record.resumeMissionIds.filter((id): id is string => typeof id === "string")
        : [],
    };
  }

  private async request(
    path: string,
    body: unknown,
  ): Promise<Record<string, unknown> | Array<Record<string, unknown>>> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.gatewayToken}`,
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(`mission request failed: HTTP ${response.status} ${text.slice(0, 200)}`);
    }
    return parsed;
  }

  private async requestGet(
    path: string,
  ): Promise<Record<string, unknown> | Array<Record<string, unknown>>> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${this.gatewayToken}`,
      },
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(`mission request failed: HTTP ${response.status} ${text.slice(0, 200)}`);
    }
    return parsed;
  }

  private async requestPatch(
    path: string,
    body: unknown,
  ): Promise<Record<string, unknown> | Array<Record<string, unknown>>> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.gatewayToken}`,
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(`mission request failed: HTTP ${response.status} ${text.slice(0, 200)}`);
    }
    return parsed;
  }
}

function isMissionActionEvent(value: unknown): value is MissionActionEvent {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.mission_id === "string" &&
    (record.event_type === "cancel_requested" ||
      record.event_type === "retry_requested" ||
      record.event_type === "unblocked")
  );
}
