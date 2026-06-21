import { describe, expect, it } from "vitest";

import { describeSpecActions } from "./describe-draft";


describe("describeSpecActions — plain-English SeamSpec summary", () => {
  it("returns one bullet per action", () => {
    const lines = describeSpecActions({
      spec_version: "0.1",
      actions: [
        { op: "modify_seam", preset_id: "coding-verification", wiring: "opt_in" },
        {
          op: "add_seam",
          preset_id: "custom:partner-approval",
          controls_refs: ["partner_approval_evidence"],
          runtime_default_on: false,
          wiring: "opt_in",
          controls_kind: "validator",
        },
      ],
    });
    expect(lines).toHaveLength(2);
  });

  it("renders an add_seam bullet with preset id + wiring + controls_refs", () => {
    const [line] = describeSpecActions({
      spec_version: "0.1",
      actions: [
        {
          op: "add_seam",
          preset_id: "custom:x",
          wiring: "opt_in",
          controls_kind: "evidence",
          controls_refs: ["ref:a", "ref:b"],
        },
      ],
    });
    expect(line).toContain("Add a brand-new preset");
    expect(line).toContain('"custom:x"');
    expect(line).toContain("wiring=opt_in");
    expect(line).toContain("controls_kind=evidence");
    expect(line).toContain("ref:a");
  });

  it("renders a modify_seam bullet listing only the changed fields", () => {
    const [line] = describeSpecActions({
      spec_version: "0.1",
      actions: [
        { op: "modify_seam", preset_id: "coding-verification", wiring: "opt_in" },
      ],
    });
    expect(line).toContain("Modify existing preset");
    expect(line).toContain('"coding-verification"');
    expect(line).toContain("wiring → opt_in");
  });

  it("calls out an empty modify_seam (no overrides) instead of silently rendering nothing", () => {
    const [line] = describeSpecActions({
      spec_version: "0.1",
      actions: [{ op: "modify_seam", preset_id: "coding-verification" }],
    });
    expect(line).toContain("no field overrides");
  });
});
