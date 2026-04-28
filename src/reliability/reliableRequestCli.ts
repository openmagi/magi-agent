import { TransportReliability } from "./TransportReliability.js";
import type { ReliableRequestSpec } from "./transportTypes.js";

function parseArgs(argv: string[]): ReliableRequestSpec {
  const spec: ReliableRequestSpec = {
    method: "GET",
    url: "",
    headers: {},
    formFields: [],
    formFiles: [],
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = argv[i + 1];
    switch (arg) {
      case "--method":
        spec.method = next ?? "GET";
        i += 1;
        break;
      case "--url":
        spec.url = next ?? "";
        i += 1;
        break;
      case "--header": {
        const header = next ?? "";
        const idx = header.indexOf(":");
        if (idx > 0) {
          spec.headers![header.slice(0, idx).trim()] = header.slice(idx + 1).trim();
        }
        i += 1;
        break;
      }
      case "--body-file":
        spec.bodyFile = next;
        i += 1;
        break;
      case "--body-text":
        spec.bodyText = next ?? "";
        i += 1;
        break;
      case "--form-field": {
        const field = next ?? "";
        const idx = field.indexOf("=");
        if (idx > 0) {
          spec.formFields!.push({
            name: field.slice(0, idx),
            value: field.slice(idx + 1),
          });
        }
        i += 1;
        break;
      }
      case "--form-file": {
        const file = next ?? "";
        const eq = file.indexOf("=");
        if (eq > 0) {
          spec.formFiles!.push({
            name: file.slice(0, eq),
            path: file.slice(eq + 1),
          });
        }
        i += 1;
        break;
      }
      case "--timeout-ms":
        spec.timeoutMs = next ? Number.parseInt(next, 10) : undefined;
        i += 1;
        break;
      default:
        break;
    }
  }

  if (!spec.url) {
    throw new Error("missing required --url");
  }

  return spec;
}

export async function main(argv = process.argv.slice(2)): Promise<void> {
  try {
    const spec = parseArgs(argv);
    const result = await new TransportReliability().request(spec);
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stdout.write(
      JSON.stringify({
        ok: false,
        classification: "fatal",
        attemptCount: 1,
        message,
        retryExhausted: false,
      }),
    );
  }
}
