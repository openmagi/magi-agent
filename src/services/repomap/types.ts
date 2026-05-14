export interface Tag {
  file: string;
  name: string;
  kind: "def" | "ref";
  line: number;
  language: string;
}

export interface Edge {
  from: string;
  to: string;
  weight: number;
}

export type SupportedLanguage = "typescript" | "javascript" | "python";

export const LANGUAGE_EXTENSIONS: Record<SupportedLanguage, string[]> = {
  typescript: [".ts", ".tsx"],
  javascript: [".js", ".jsx", ".mjs", ".cjs"],
  python: [".py"],
};

export const EXTENSION_TO_LANGUAGE: Record<string, SupportedLanguage> = {};
for (const [lang, exts] of Object.entries(LANGUAGE_EXTENSIONS)) {
  for (const ext of exts) {
    EXTENSION_TO_LANGUAGE[ext] = lang as SupportedLanguage;
  }
}

export const SKIP_DIRS = new Set([
  ".git",
  "node_modules",
  ".next",
  "dist",
  "build",
  "coverage",
  ".turbo",
  ".venv",
  "__pycache__",
  ".core-agent",
]);

export const GENERATED_VENDOR_PATTERNS = [
  /\.generated\./,
  /\.min\./,
  /vendor\//,
  /generated\//,
  /\.d\.ts$/,
];

export const TEST_FILE_PATTERNS = [
  /\.test\.[jt]sx?$/,
  /\.spec\.[jt]sx?$/,
  /__tests__\//,
  /test_.*\.py$/,
  /_test\.py$/,
];
