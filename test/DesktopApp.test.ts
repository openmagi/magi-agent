import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const tauriConfig = JSON.parse(
  readFileSync(join(process.cwd(), "apps/desktop/src-tauri/tauri.conf.json"), "utf8"),
) as {
  build?: { frontendDist?: string; devUrl?: string };
  app?: { windows?: Array<{ url?: string }> };
};
const launcherHtml = readFileSync(
  join(process.cwd(), "apps/desktop/launcher/index.html"),
  "utf8",
);

describe("Magi desktop shell", () => {
  it("packages a local launcher instead of hardcoding the runtime URL in Tauri config", () => {
    expect(tauriConfig.build?.frontendDist).toBe("../launcher");
    expect(tauriConfig.app?.windows?.[0]?.url).toBe("index.html");
    expect(JSON.stringify(tauriConfig)).not.toContain("127.0.0.1:8080/app");
  });

  it("lets users configure the runtime URL from the packaged launcher", () => {
    expect(launcherHtml).toContain('id="runtime-url"');
    expect(launcherHtml).toContain("magi.desktop.runtimeUrl");
    expect(launcherHtml).toContain("http://127.0.0.1:8080/app");
  });
});
