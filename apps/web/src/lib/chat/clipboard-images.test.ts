import { describe, expect, it } from "vitest";
import { extractClipboardImageFiles } from "./clipboard-images";

function clipboardItem(file: File): DataTransferItem {
  return {
    kind: "file",
    type: file.type,
    getAsFile: () => file,
  } as DataTransferItem;
}

describe("clipboard image extraction", () => {
  it("extracts pasted image files from clipboard items", () => {
    const file = new File(["image"], "screenshot.png", { type: "image/png" });

    const images = extractClipboardImageFiles({
      items: [clipboardItem(file)] as unknown as DataTransferItemList,
      files: [] as unknown as FileList,
    });

    expect(images).toHaveLength(1);
    expect(images[0].name).toBe("screenshot.png");
    expect(images[0].type).toBe("image/png");
  });

  it("assigns a deterministic filename when clipboard images are unnamed", () => {
    const file = new File(["image"], "", { type: "image/jpeg" });

    const images = extractClipboardImageFiles({
      items: [clipboardItem(file)] as unknown as DataTransferItemList,
      files: [] as unknown as FileList,
    });

    expect(images[0].name).toBe("clipboard-image-1.jpg");
    expect(images[0].type).toBe("image/jpeg");
  });

  it("ignores non-image clipboard files", () => {
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });

    const images = extractClipboardImageFiles({
      items: [clipboardItem(file)] as unknown as DataTransferItemList,
      files: [] as unknown as FileList,
    });

    expect(images).toEqual([]);
  });
});
