import { describe, expect, it } from "vitest";
import { Utf8StreamCapture } from "./Utf8StreamCapture.js";

function splitAtNeedleByte(value: string, needle: string, byteOffsetInNeedle: number): Buffer[] {
  const bytes = Buffer.from(value, "utf8");
  const needleBytes = Buffer.from(needle, "utf8");
  const needleStart = bytes.indexOf(needleBytes);
  if (needleStart < 0) throw new Error(`needle not found: ${needle}`);
  const splitAt = needleStart + byteOffsetInNeedle;
  return [bytes.subarray(0, splitAt), bytes.subarray(splitAt)];
}

describe("Utf8StreamCapture", () => {
  it("preserves Korean text when tool stdout chunks split a UTF-8 character", () => {
    const capture = new Utf8StreamCapture(1024);
    const chunks = splitAtNeedleByte("접속 및 툴즈 분석으로 확인", "툴", 2);

    for (const chunk of chunks) capture.write(chunk);
    capture.end();

    expect(capture.text).toBe("접속 및 툴즈 분석으로 확인");
    expect(capture.text).not.toContain("�");
    expect(capture.truncated).toBe(false);
  });

  it("marks output as truncated when the decoded text exceeds the limit", () => {
    const capture = new Utf8StreamCapture(5);

    capture.write(Buffer.from("abcdef", "utf8"));
    capture.end();

    expect(capture.text).toBe("abcde");
    expect(capture.truncated).toBe(true);
  });
});
