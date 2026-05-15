import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("SettingsForm", () => {
  it("keeps delete account modal state inside the bot settings form", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/settings-form.tsx"), "utf8");
    const botFormStart = source.indexOf("function BotSettingsForm");
    const botFormReturn = source.indexOf("return (", botFormStart);

    expect(botFormStart).toBeGreaterThanOrEqual(0);
    expect(botFormReturn).toBeGreaterThan(botFormStart);
    expect(source.slice(botFormStart, botFormReturn)).toContain(
      "const [deleteModalOpen, setDeleteModalOpen] = useState(false);",
    );
  });
});
