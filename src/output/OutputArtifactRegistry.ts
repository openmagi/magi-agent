import fs from "node:fs/promises";
import path from "node:path";
import { monotonicFactory } from "ulid";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type {
  DeliveryMutation,
  DeliveryRecord,
  OutputArtifactRecord,
  RegisterOutputArtifactInput,
} from "./outputTypes.js";

const ulid = monotonicFactory();

interface OutputIndexFile {
  schemaVersion: 1;
  artifacts: OutputArtifactRecord[];
}

function makeDeliveryRecord(
  mutation: DeliveryMutation,
  now: number,
  previous?: DeliveryRecord,
): DeliveryRecord {
  const status = mutation.status ?? "failed";
  return {
    target: mutation.target,
    status,
    attemptCount: mutation.attemptCount,
    ...(mutation.externalId ? { externalId: mutation.externalId } : {}),
    ...(mutation.marker ? { marker: mutation.marker } : {}),
    ...(mutation.errorMessage ? { errorMessage: mutation.errorMessage } : {}),
    ...(status === "sent"
      ? { deliveredAt: now }
      : previous?.deliveredAt
        ? { deliveredAt: previous.deliveredAt }
        : {}),
    updatedAt: now,
  };
}

export class OutputArtifactRegistry {
  constructor(private readonly workspaceRoot: string) {}

  private dir(): string {
    return path.join(this.workspaceRoot, "output-artifacts");
  }

  private indexPath(): string {
    return path.join(this.dir(), "index.json");
  }

  private async readIndex(): Promise<OutputIndexFile> {
    try {
      const raw = await fs.readFile(this.indexPath(), "utf8");
      const parsed = JSON.parse(raw) as Partial<OutputIndexFile>;
      if (parsed.schemaVersion === 1 && Array.isArray(parsed.artifacts)) {
        return parsed as OutputIndexFile;
      }
      return { schemaVersion: 1, artifacts: [] };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { schemaVersion: 1, artifacts: [] };
      }
      throw error;
    }
  }

  private async writeIndex(index: OutputIndexFile): Promise<void> {
    await fs.mkdir(this.dir(), { recursive: true });
    await atomicWriteJson(this.indexPath(), index);
  }

  async register(input: RegisterOutputArtifactInput): Promise<OutputArtifactRecord> {
    const now = Date.now();
    const record: OutputArtifactRecord = {
      artifactId: ulid(),
      ...input,
      deliveries: [],
      createdAt: now,
      updatedAt: now,
    };
    const index = await this.readIndex();
    index.artifacts.push(record);
    await this.writeIndex(index);
    return record;
  }

  async get(artifactId: string): Promise<OutputArtifactRecord> {
    const index = await this.readIndex();
    const found = index.artifacts.find((artifact) => artifact.artifactId === artifactId);
    if (!found) {
      throw new Error(`output artifact not found: ${artifactId}`);
    }
    return found;
  }

  async listUndelivered(sessionKey: string, turnId?: string): Promise<OutputArtifactRecord[]> {
    const index = await this.readIndex();
    return index.artifacts.filter((artifact) => {
      if (artifact.sessionKey !== sessionKey) return false;
      if (turnId && artifact.turnId !== turnId) return false;
      return (
        artifact.deliveries.length === 0 ||
        artifact.deliveries.every((delivery) => delivery.status !== "sent")
      );
    });
  }

  async markDeliveryPending(
    artifactId: string,
    mutation: DeliveryMutation,
  ): Promise<OutputArtifactRecord> {
    return this.markDeliveryResult(artifactId, {
      ...mutation,
      status: mutation.attemptCount > 1 ? "retrying" : "pending",
    });
  }

  async markDeliveryResult(
    artifactId: string,
    mutation: DeliveryMutation,
  ): Promise<OutputArtifactRecord> {
    const index = await this.readIndex();
    const found = index.artifacts.find((artifact) => artifact.artifactId === artifactId);
    if (!found) {
      throw new Error(`output artifact not found: ${artifactId}`);
    }

    const now = Date.now();
    const previous = found.deliveries.find((delivery) => delivery.target === mutation.target);
    const next = makeDeliveryRecord(mutation, now, previous);

    found.deliveries = [
      ...found.deliveries.filter((delivery) => delivery.target !== mutation.target),
      next,
    ];
    found.updatedAt = now;

    await this.writeIndex(index);
    return found;
  }
}
