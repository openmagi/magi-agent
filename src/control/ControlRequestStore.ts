import crypto from "node:crypto";
import type {
  ControlRequestDecision,
  ControlRequestKind,
  ControlRequestRecord,
  ControlRequestSource,
} from "./ControlEvents.js";
import { ControlEventLedger } from "./ControlEventLedger.js";
import {
  projectControlEvents,
  type ControlProjection,
} from "./ControlProjection.js";

export interface CreateControlRequestInput {
  kind: ControlRequestKind;
  turnId?: string;
  sessionKey: string;
  channelName?: string;
  source: ControlRequestSource;
  prompt: string;
  proposedInput?: unknown;
  expiresAt: number;
  idempotencyKey?: string;
}

export interface ResolveControlRequestInput {
  decision: ControlRequestDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
}

export class ControlRequestStore {
  constructor(private readonly opts: { ledger: ControlEventLedger }) {}

  async create(input: CreateControlRequestInput): Promise<ControlRequestRecord> {
    if (input.idempotencyKey) {
      const events = await this.opts.ledger.readAll();
      const existing = events.find(
        (event) =>
          event.type === "control_request_created" &&
          event.idempotencyKey === input.idempotencyKey,
      );
      if (existing?.type === "control_request_created") {
        return (
          (await this.project()).requests[existing.request.requestId] ??
          existing.request
        );
      }
    }

    const now = Date.now();
    const record: ControlRequestRecord = {
      requestId: `cr_${crypto.randomUUID().replace(/-/g, "")}`,
      kind: input.kind,
      state: "pending",
      sessionKey: input.sessionKey,
      turnId: input.turnId,
      channelName: input.channelName,
      source: input.source,
      prompt: input.prompt,
      proposedInput: input.proposedInput,
      createdAt: now,
      expiresAt: input.expiresAt,
    };
    await this.opts.ledger.append({
      type: "control_request_created",
      turnId: input.turnId,
      idempotencyKey: input.idempotencyKey,
      request: record,
    });
    return record;
  }

  async resolve(
    requestId: string,
    input: ResolveControlRequestInput,
  ): Promise<ControlRequestRecord> {
    const projection = await this.project();
    const existing = projection.requests[requestId];
    if (!existing) {
      throw new Error(`control request not found: ${requestId}`);
    }
    if (existing.state !== "pending") {
      return existing;
    }
    if (existing.expiresAt <= Date.now()) {
      await this.opts.ledger.append({
        type: "control_request_timed_out",
        turnId: existing.turnId,
        requestId,
      });
      return (await this.project()).requests[requestId]!;
    }
    await this.opts.ledger.append({
      type: "control_request_resolved",
      turnId: existing.turnId,
      requestId,
      decision: input.decision,
      feedback: input.feedback,
      updatedInput: input.updatedInput,
      answer: input.answer,
    });
    return (await this.project()).requests[requestId]!;
  }

  async cancel(requestId: string, reason: string): Promise<ControlRequestRecord> {
    const projection = await this.project();
    const existing = projection.requests[requestId];
    if (!existing) throw new Error(`control request not found: ${requestId}`);
    if (existing.state !== "pending") return existing;
    await this.opts.ledger.append({
      type: "control_request_cancelled",
      turnId: existing.turnId,
      requestId,
      reason,
    });
    return (await this.project()).requests[requestId]!;
  }

  async pending(now = Date.now()): Promise<ControlRequestRecord[]> {
    return (await this.project(now)).pendingRequests;
  }

  async project(now = Date.now()): Promise<ControlProjection> {
    const events = await this.opts.ledger.readAll();
    return projectControlEvents(events, { now });
  }
}
