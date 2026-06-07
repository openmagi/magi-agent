import { describe, expect, it } from "vitest";
import {
  KB_UPLOAD_ACCEPT,
  prepareKnowledgeUploadFile,
  resolveKnowledgeUploadMimeType,
} from "./upload-mime";

describe("resolveKnowledgeUploadMimeType", () => {
  it("maps xlsx octet-stream uploads back to the spreadsheet mime type", () => {
    expect(resolveKnowledgeUploadMimeType({
      name: "sales-report.xlsx",
      type: "application/octet-stream",
    })).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  });

  it("maps empty xlsx uploads back to the spreadsheet mime type", () => {
    expect(resolveKnowledgeUploadMimeType({
      name: "sales-report.xlsx",
      type: "",
    })).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  });

  it("maps xls octet-stream uploads back to the spreadsheet mime type", () => {
    expect(resolveKnowledgeUploadMimeType({
      name: "legacy-report.xls",
      type: "application/octet-stream",
    })).toBe("application/vnd.ms-excel");
  });

  it("maps png octet-stream uploads back to the image mime type", () => {
    expect(resolveKnowledgeUploadMimeType({
      name: "whiteboard.png",
      type: "application/octet-stream",
    })).toBe("image/png");
  });

  it("maps source-code octet-stream uploads to searchable text MIME types", () => {
    expect(resolveKnowledgeUploadMimeType({
      name: "script_part_a.py",
      type: "application/octet-stream",
    })).toBe("text/plain");
    expect(resolveKnowledgeUploadMimeType({
      name: "script_part_a.rpy",
      type: "application/octet-stream",
    })).toBe("text/plain");
    expect(resolveKnowledgeUploadMimeType({
      name: "agent.cpp",
      type: "application/octet-stream",
    })).toBe("text/plain");
    expect(resolveKnowledgeUploadMimeType({
      name: "schema.xml",
      type: "application/octet-stream",
    })).toBe("application/xml");
  });
});

describe("KB_UPLOAD_ACCEPT", () => {
  it("includes excel and common image file extensions", () => {
    expect(KB_UPLOAD_ACCEPT).toContain(".xlsx");
    expect(KB_UPLOAD_ACCEPT).toContain(".xls");
    expect(KB_UPLOAD_ACCEPT).toContain(".jpg");
    expect(KB_UPLOAD_ACCEPT).toContain(".jpeg");
    expect(KB_UPLOAD_ACCEPT).toContain(".png");
    expect(KB_UPLOAD_ACCEPT).toContain(".gif");
    expect(KB_UPLOAD_ACCEPT).toContain(".webp");
  });

  it("includes common source-code and markup extensions", () => {
    expect(KB_UPLOAD_ACCEPT).toContain(".py");
    expect(KB_UPLOAD_ACCEPT).toContain(".rpy");
    expect(KB_UPLOAD_ACCEPT).toContain(".c");
    expect(KB_UPLOAD_ACCEPT).toContain(".cpp");
    expect(KB_UPLOAD_ACCEPT).toContain(".ts");
    expect(KB_UPLOAD_ACCEPT).toContain(".tsx");
    expect(KB_UPLOAD_ACCEPT).toContain(".xml");
  });
});

describe("prepareKnowledgeUploadFile", () => {
  it("retypes octet-stream office uploads before direct upload", () => {
    const input = new File(["excel"], "financials.xlsx", {
      type: "application/octet-stream",
      lastModified: 123,
    });

    const prepared = prepareKnowledgeUploadFile(input);

    expect(prepared).not.toBe(input);
    expect(prepared.name).toBe("financials.xlsx");
    expect(prepared.type).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    expect(prepared.lastModified).toBe(123);
  });

  it("retypes octet-stream image uploads before direct upload", () => {
    const input = new File(["image"], "whiteboard.png", {
      type: "application/octet-stream",
      lastModified: 456,
    });

    const prepared = prepareKnowledgeUploadFile(input);

    expect(prepared).not.toBe(input);
    expect(prepared.type).toBe("image/png");
    expect(prepared.lastModified).toBe(456);
  });
});
