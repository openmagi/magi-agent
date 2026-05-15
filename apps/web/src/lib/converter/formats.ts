export type ConverterSourceFormat = "pdf" | "docx";
export type ConverterTargetFormat = "docx" | "hwpx";

export const CONVERTER_ACCEPTED_EXTENSIONS = [".pdf", ".docx"] as const;

export const CONVERTER_ACCEPTED_MIMES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
] as const;

const MIME_TO_SOURCE_FORMAT: Record<string, ConverterSourceFormat> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
};

const EXT_TO_SOURCE_FORMAT: Record<string, ConverterSourceFormat> = {
  ".pdf": "pdf",
  ".docx": "docx",
};

const TARGETS_BY_SOURCE: Record<ConverterSourceFormat, ConverterTargetFormat[]> = {
  pdf: ["docx", "hwpx"],
  docx: ["hwpx"],
};

export function isSupportedConverterTargetFormat(value: string): value is ConverterTargetFormat {
  return value === "docx" || value === "hwpx";
}

export function getConverterSourceFormat(
  mime: string | undefined,
  filename: string,
): ConverterSourceFormat | null {
  const normalizedMime = (mime || "").toLowerCase();
  if (MIME_TO_SOURCE_FORMAT[normalizedMime]) return MIME_TO_SOURCE_FORMAT[normalizedMime];

  const dot = filename.lastIndexOf(".");
  const ext = dot >= 0 ? filename.slice(dot).toLowerCase() : "";
  return EXT_TO_SOURCE_FORMAT[ext] ?? null;
}

export function getConverterTargetFormats(sourceFormat: ConverterSourceFormat): ConverterTargetFormat[] {
  return [...TARGETS_BY_SOURCE[sourceFormat]];
}

export function isSupportedConverterPair(
  sourceFormat: ConverterSourceFormat,
  targetFormat: string,
): targetFormat is ConverterTargetFormat {
  return isSupportedConverterTargetFormat(targetFormat)
    && TARGETS_BY_SOURCE[sourceFormat].includes(targetFormat);
}
