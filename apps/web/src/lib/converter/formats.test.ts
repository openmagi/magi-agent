import { describe, expect, it } from "vitest";
import {
  getConverterSourceFormat,
  getConverterTargetFormats,
  isSupportedConverterPair,
  isSupportedConverterTargetFormat,
} from "./formats";

describe("converter format contract", () => {
  it("supports only implemented source and target pairs", () => {
    expect(getConverterTargetFormats("pdf")).toEqual(["docx", "hwpx"]);
    expect(getConverterTargetFormats("docx")).toEqual(["hwpx"]);

    expect(isSupportedConverterPair("pdf", "docx")).toBe(true);
    expect(isSupportedConverterPair("pdf", "hwpx")).toBe(true);
    expect(isSupportedConverterPair("docx", "hwpx")).toBe(true);
    expect(isSupportedConverterPair("docx", "pdf")).toBe(false);
  });

  it("rejects target formats the worker does not implement", () => {
    expect(isSupportedConverterTargetFormat("docx")).toBe(true);
    expect(isSupportedConverterTargetFormat("hwpx")).toBe(true);
    expect(isSupportedConverterTargetFormat("pdf")).toBe(false);
    expect(isSupportedConverterTargetFormat("hwp")).toBe(false);
  });

  it("resolves only PDF and DOCX sources", () => {
    expect(getConverterSourceFormat("application/pdf", "file.bin")).toBe("pdf");
    expect(getConverterSourceFormat("", "file.PDF")).toBe("pdf");
    expect(
      getConverterSourceFormat(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "file.bin",
      ),
    ).toBe("docx");
    expect(getConverterSourceFormat("", "file.docx")).toBe("docx");
    expect(getConverterSourceFormat("application/x-hwp", "file.hwp")).toBeNull();
    expect(getConverterSourceFormat("application/haansofthwpx", "file.hwpx")).toBeNull();
  });
});
