export type OutputKind = "document" | "spreadsheet" | "file";
export type OutputFormat = "html" | "docx" | "hwpx" | "pdf" | "xlsx" | "csv" | "tsv";
export type PreviewKind = "inline-html" | "inline-markdown" | "download-only" | "none";
export type DeliveryTarget = "chat" | "kb";
export type DeliveryStatus = "pending" | "retrying" | "sent" | "failed";

export interface DeliveryRecord {
  target: DeliveryTarget;
  status: DeliveryStatus;
  attemptCount: number;
  externalId?: string;
  marker?: string;
  errorMessage?: string;
  deliveredAt?: number;
  updatedAt: number;
}

export interface OutputArtifactRecord {
  artifactId: string;
  sessionKey: string;
  turnId: string;
  kind: OutputKind;
  format: OutputFormat;
  title: string;
  filename: string;
  mimeType: string;
  workspacePath: string;
  previewKind: PreviewKind;
  createdByTool: string;
  sourceKind: string;
  deliveries: DeliveryRecord[];
  createdAt: number;
  updatedAt: number;
}

export interface RegisterOutputArtifactInput {
  sessionKey: string;
  turnId: string;
  kind: OutputKind;
  format: OutputFormat;
  title: string;
  filename: string;
  mimeType: string;
  workspacePath: string;
  previewKind: PreviewKind;
  createdByTool: string;
  sourceKind: string;
}

export interface DeliveryMutation {
  target: DeliveryTarget;
  attemptCount: number;
  status?: DeliveryStatus;
  externalId?: string;
  marker?: string;
  errorMessage?: string;
}
