export type CanonicalMarkdownBlock =
  | {
      type: "heading";
      level: 1 | 2 | 3 | 4 | 5 | 6;
      children: CanonicalInline[];
    }
  | { type: "paragraph"; children: CanonicalInline[] }
  | { type: "blockquote"; children: CanonicalMarkdownBlock[] }
  | { type: "list"; ordered: boolean; items: CanonicalMarkdownBlock[][] }
  | { type: "code"; lang?: string; value: string }
  | { type: "thematic_break" }
  | {
      type: "table";
      align: Array<"left" | "center" | "right" | null>;
      rows: CanonicalTableCell[][];
    };

export interface CanonicalTableCell {
  header: boolean;
  children: CanonicalInline[];
}

export type CanonicalInline =
  | { type: "text"; value: string }
  | { type: "strong"; children: CanonicalInline[] }
  | { type: "emphasis"; children: CanonicalInline[] }
  | { type: "inline_code"; value: string }
  | { type: "link"; url: string; children: CanonicalInline[] }
  | { type: "image"; url: string; alt: string };

export type CanonicalMarkdownPreset =
  | "memo"
  | "report"
  | "investment_committee"
  | "plain";
export type CanonicalMarkdownLocale =
  | "en-US"
  | "ko-KR"
  | "ja-JP"
  | "zh-CN"
  | "es-ES";
export type CanonicalMarkdownPageSize = "A4" | "Letter";
export type CanonicalDocxMode = "editable" | "fixed_layout";

export interface CanonicalMarkdownPageOptions {
  size: CanonicalMarkdownPageSize;
  margin: string;
}

export interface CanonicalMarkdownRenderOptions {
  title: string;
  preset: CanonicalMarkdownPreset;
  locale: CanonicalMarkdownLocale;
  page: CanonicalMarkdownPageOptions;
}

export interface CanonicalMarkdownDocument {
  sourceMarkdown: string;
  sourceHash: string;
  blocks: CanonicalMarkdownBlock[];
}

export interface CanonicalMarkdownHtmlOutput {
  html: string;
  css: string;
  rendererVersion: string;
}

export interface BrowserRenderRequest {
  html: string;
  css: string;
  title: string;
  page: CanonicalMarkdownPageOptions;
  locale: CanonicalMarkdownLocale;
}

export interface BrowserRenderResult {
  pdfBase64: string;
  screenshots: Array<{
    page: number;
    pngBase64: string;
    width: number;
    height: number;
  }>;
  pageCount: number;
  rendererVersion: string;
}

export interface CanonicalMarkdownQa {
  status: "passed" | "passed_with_warnings";
  sourceHash: string;
  rendererVersion: string;
  warnings: string[];
  pageCount?: number;
}
