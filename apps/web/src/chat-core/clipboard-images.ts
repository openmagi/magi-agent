const IMAGE_EXTENSIONS: Record<string, string> = {
  "image/gif": "gif",
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};

function isSupportedClipboardImageType(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(IMAGE_EXTENSIONS, type);
}

function withClipboardFilename(file: File, index: number, fallbackType?: string): File {
  const type = file.type || fallbackType || "";
  const extension = IMAGE_EXTENSIONS[type] ?? "png";
  const name = file.name.trim() || `clipboard-image-${index}.${extension}`;
  if (name === file.name && type === file.type) return file;
  return new File([file], name, {
    type,
    lastModified: file.lastModified,
  });
}

export function extractClipboardImageFiles(
  clipboardData: Pick<DataTransfer, "items" | "files"> | null,
): File[] {
  if (!clipboardData) return [];

  const itemImages: File[] = [];
  for (const item of Array.from(clipboardData.items ?? [])) {
    if (item.kind !== "file" || !isSupportedClipboardImageType(item.type)) continue;
    const file = item.getAsFile();
    if (!file) continue;
    itemImages.push(withClipboardFilename(file, itemImages.length + 1, item.type));
  }
  if (itemImages.length > 0) return itemImages;

  const fileImages: File[] = [];
  for (const file of Array.from(clipboardData.files ?? [])) {
    if (!isSupportedClipboardImageType(file.type)) continue;
    fileImages.push(withClipboardFilename(file, fileImages.length + 1));
  }
  return fileImages;
}
