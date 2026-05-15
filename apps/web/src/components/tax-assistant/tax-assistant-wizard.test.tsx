import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TaxAssistantWizard } from "./tax-assistant-wizard";

describe("TaxAssistantWizard", () => {
  it("renders one focused next action instead of a full step grid", () => {
    const html = renderToStaticMarkup(<TaxAssistantWizard />);

    expect(html).toContain("지금 할 일");
    expect(html).toContain("신고서 읽기 시작");
    expect(html).toContain("비밀번호 저장 없음");
    expect(html).toContain("비회원");
    expect(html).not.toContain("STEP 1");
    expect(html).not.toContain("STEP 7");
  });

  it("renders the guest flow promise and safety boundaries without making them the main task", () => {
    const html = renderToStaticMarkup(<TaxAssistantWizard />);

    expect(html).toContain("홈택스 로그인은 사용자가 직접");
    expect(html).toContain("최종 제출");
    expect(html).toContain("납부");
    expect(html).toContain("환급계좌 확정");
  });

  it("does not distract first-time users with report export options", () => {
    const html = renderToStaticMarkup(<TaxAssistantWizard />);

    expect(html).not.toContain("중간저장 완료 후");
    expect(html).not.toContain("PDF");
    expect(html).not.toContain("ZIP");
  });
});
