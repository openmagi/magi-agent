import type { BrowserRenderRequest } from "./canonicalMarkdownTypes.js";

export interface BrowserRenderClientInput {
  chatProxyUrl: string;
  gatewayToken: string;
  request: BrowserRenderRequest;
  fetchImpl?: typeof fetch;
}

export interface BrowserRenderClientOutput {
  pdfBytes: Buffer;
  screenshots: Array<{ page: number; pngBytes: Buffer; width: number; height: number }>;
  pageCount: number;
  rendererVersion: string;
}

interface RawBrowserWorkerRenderResponse {
  pdfBase64?: unknown;
  screenshots?: unknown;
  pageCount?: unknown;
  rendererVersion?: unknown;
  error?: unknown;
}

export async function renderCanonicalMarkdownViaChatProxy(
  input: BrowserRenderClientInput,
): Promise<BrowserRenderClientOutput> {
  if (!input.chatProxyUrl) {
    throw new Error(
      "browser render service URL is not configured — PDF rendering via browser is not available in this environment. " +
      "Set the render service URL or use HTML/DOCX output formats instead.",
    );
  }
  if (!input.gatewayToken) {
    throw new Error("gateway token is required for canonical Markdown rendering");
  }
  const base = input.chatProxyUrl.replace(/\/+$/, "");
  const fetcher = input.fetchImpl ?? fetch;
  const res = await fetcher(`${base}/v1/integrations/browser-render/canonical-markdown`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${input.gatewayToken}`,
    },
    body: JSON.stringify(input.request),
  });
  const raw = (await res.json()) as RawBrowserWorkerRenderResponse;
  if (!res.ok) {
    const message = typeof raw.error === "string" ? raw.error : res.statusText;
    throw new Error(`browser render service failed ${res.status}: ${message}`);
  }
  if (typeof raw.pdfBase64 !== "string" || !raw.pdfBase64) {
    throw new Error("browser-worker render response missing pdfBase64");
  }
  if (!Array.isArray(raw.screenshots)) {
    throw new Error("browser-worker render response missing screenshots");
  }
  return {
    pdfBytes: Buffer.from(raw.pdfBase64, "base64"),
    screenshots: raw.screenshots.map((entry) => {
      const item = entry as {
        page?: unknown;
        pngBase64?: unknown;
        width?: unknown;
        height?: unknown;
      };
      return {
        page: typeof item.page === "number" ? item.page : 1,
        pngBytes: Buffer.from(
          typeof item.pngBase64 === "string" ? item.pngBase64 : "",
          "base64",
        ),
        width: typeof item.width === "number" ? item.width : 0,
        height: typeof item.height === "number" ? item.height : 0,
      };
    }),
    pageCount: typeof raw.pageCount === "number" ? raw.pageCount : 1,
    rendererVersion:
      typeof raw.rendererVersion === "string"
        ? raw.rendererVersion
        : "browser-worker-playwright/unknown",
  };
}
