import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Select } from "./select";

const OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "fireworks", label: "Fireworks" },
];

describe("Select", () => {
  it("renders the selected option's label in the trigger", () => {
    const html = renderToStaticMarkup(
      <Select value="openai" options={OPTIONS} onChange={() => {}} />,
    );
    expect(html).toContain("OpenAI");
  });

  it("falls back to the placeholder when no option matches", () => {
    const html = renderToStaticMarkup(
      <Select value="" options={OPTIONS} onChange={() => {}} placeholder="Pick one" />,
    );
    expect(html).toContain("Pick one");
  });

  it("keeps the listbox closed by default", () => {
    const html = renderToStaticMarkup(
      <Select value="anthropic" options={OPTIONS} onChange={() => {}} />,
    );
    expect(html).not.toContain('role="listbox"');
    expect(html).toContain('aria-expanded="false"');
  });

  it("renders the option list and marks the selected option when open", () => {
    const html = renderToStaticMarkup(
      <Select value="fireworks" options={OPTIONS} onChange={() => {}} defaultOpen />,
    );
    expect(html).toContain('role="listbox"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("Anthropic");
    expect(html).toContain("OpenAI");
    expect(html).toContain("Fireworks");
    // The selected option is flagged for assistive tech.
    expect(html).toContain('aria-selected="true"');
  });

  it("renders an upward menu when menuPlacement is top", () => {
    const html = renderToStaticMarkup(
      <Select value="anthropic" options={OPTIONS} onChange={() => {}} defaultOpen menuPlacement="top" />,
    );
    expect(html).toContain("bottom-full");
    expect(html).not.toContain("top-full");
  });

  it("exposes the label text when provided", () => {
    const html = renderToStaticMarkup(
      <Select label="Provider" value="anthropic" options={OPTIONS} onChange={() => {}} />,
    );
    expect(html).toContain("Provider");
  });
});
