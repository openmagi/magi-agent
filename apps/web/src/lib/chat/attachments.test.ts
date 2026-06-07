import { describe, expect, it } from "vitest";
import { validateFile } from "./attachments";

describe("chat attachment validation", () => {
  it("accepts pptx files for web chat uploads", () => {
    const file = new File(
      ["slide deck"],
      "deck.pptx",
      { type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts xlsx files for web chat uploads", () => {
    const file = new File(
      ["sheet"],
      "report.xlsx",
      { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts xlsx files uploaded as octet-stream by filename fallback", () => {
    const file = new File(
      ["sheet"],
      "report.xlsx",
      { type: "application/octet-stream" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts legacy xls files for web chat uploads", () => {
    const file = new File(
      ["legacy sheet"],
      "report.xls",
      { type: "application/vnd.ms-excel" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts mp3 files for consultation audio uploads", () => {
    const file = new File(["audio"], "client-call.mp3", { type: "audio/mpeg" });

    expect(validateFile(file)).toBeNull();
  });

  it("accepts m4a files uploaded as octet-stream by filename fallback", () => {
    const file = new File(
      ["audio"],
      "client-call.m4a",
      { type: "application/octet-stream" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts source files uploaded as octet-stream by filename fallback", () => {
    expect(validateFile(new File(["print('hi')"], "script_part_a.py", {
      type: "application/octet-stream",
    }))).toBeNull();
    expect(validateFile(new File(["label start:"], "script_part_a.rpy", {
      type: "application/octet-stream",
    }))).toBeNull();
    expect(validateFile(new File(["int main() {}"], "agent.cpp", {
      type: "application/octet-stream",
    }))).toBeNull();
  });

  it("accepts tar.gz archives for web chat downloads and uploads", () => {
    expect(validateFile(new File(["archive"], "vn_hotel_all_rpy.tar.gz", {
      type: "application/gzip",
    }))).toBeNull();
    expect(validateFile(new File(["archive"], "vn_hotel_all_rpy.tgz", {
      type: "application/octet-stream",
    }))).toBeNull();
  });

  it("accepts browser-mislabelled TypeScript files by filename fallback", () => {
    const file = new File(
      ["export const answer: number = 42;"],
      "answer.ts",
      { type: "video/mp2t" },
    );

    expect(validateFile(file)).toBeNull();
  });

  it("accepts XML uploads with XML MIME types", () => {
    expect(validateFile(new File(["<root />"], "schema.xml", {
      type: "application/xml",
    }))).toBeNull();
    expect(validateFile(new File(["<root />"], "schema.xml", {
      type: "text/xml",
    }))).toBeNull();
  });
});
