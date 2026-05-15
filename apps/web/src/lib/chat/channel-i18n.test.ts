import { describe, expect, it } from "vitest";

import { DEFAULT_CHANNELS, localizeChannel } from "./channel-i18n";

describe("channel i18n defaults", () => {
  it("treats only general as a built-in default channel", () => {
    expect(DEFAULT_CHANNELS).toEqual(["general"]);
  });

  it("does not localize legacy seeded channel names as built-ins", () => {
    expect(localizeChannel("random", "Random", "ko")).toBe("Random");
  });
});
