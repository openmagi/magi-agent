"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buildMockInputPlan, runMockApprovedInput, runMockMiddleSave } from "@/lib/tax-assistant/mock-runner";
import { createGuestTaxSession, taxAssistantReducer } from "@/lib/tax-assistant/session-machine";
import type { TaxAssistantSession } from "@/lib/tax-assistant/types";

function createInitialSession(): TaxAssistantSession {
  return createGuestTaxSession({
    sessionId: "tax_guest_preview",
    nowIso: "2026-05-01T09:00:00.000Z",
  });
}

type FocusedTask = {
  progress: number;
  progressLabel: string;
  eyebrow: string;
  title: string;
  body: string;
  primaryLabel: string;
  primaryDisabled?: boolean;
  onPrimary?: () => void;
  note: string;
};

export function TaxAssistantWizard() {
  const [session, setSession] = useState<TaxAssistantSession>(() => createInitialSession());

  const approvedCount = useMemo(
    () => session.inputPlan.filter((row) => row.approved).length,
    [session.inputPlan],
  );

  const readFiling = () => {
    setSession((current) => taxAssistantReducer(current, {
      type: "read_filing",
      ...buildMockInputPlan(),
    }));
  };

  const approveAll = () => {
    setSession((current) => taxAssistantReducer(current, {
      type: "approve_rows",
      rowIds: current.inputPlan.map((row) => row.id),
    }));
  };

  const runMiddleSave = () => {
    setSession((current) => {
      const input = runMockApprovedInput(current, new Date().toISOString());
      if (!input.ok) {
        return {
          ...current,
          error: input.error,
        };
      }
      return runMockMiddleSave(input.session, new Date().toISOString());
    });
  };

  const openReportGate = () => {
    setSession((current) => taxAssistantReducer(current, { type: "open_report_gate" }));
  };

  const candidateCount = session.inputPlan.length;
  const riskyRows = session.inputPlan.filter((row) => row.riskFlag);
  const completedEvents = session.automationEvents.filter((event) => event.status === "done");

  const task: FocusedTask = (() => {
    if (session.step === "report_gate") {
      return {
        progress: 100,
        progressLabel: "저장 준비 완료",
        eyebrow: "마지막 확인",
        title: "리포트 저장은 로그인 후 이어져요",
        body: "입력 전후 값 비교, 증빙 묶음, 중간저장 확인 기록을 계정에 보관할 수 있습니다.",
        primaryLabel: "대시보드에서 이어가기",
        primaryDisabled: true,
        note: "최종 제출, 납부, 환급계좌 확정은 사용자가 직접 진행합니다.",
      };
    }

    if (session.middleSave?.confirmed) {
      return {
        progress: 86,
        progressLabel: "중간저장 완료",
        eyebrow: "완료",
        title: "홈택스 중간저장까지 끝났어요",
        body: session.middleSave.confirmationText,
        primaryLabel: "리포트 저장 옵션 보기",
        onPrimary: openReportGate,
        note: "저장된 신고서는 홈택스에서 직접 확인할 수 있습니다.",
      };
    }

    if (approvedCount > 0) {
      return {
        progress: 68,
        progressLabel: `${approvedCount}개 승인됨`,
        eyebrow: "다음 할 일",
        title: "승인한 값만 입력하고 중간저장할게요",
        body: "Open Magi가 홈택스 화면에 승인된 항목만 입력하고, 값 대조 후 중간저장까지만 진행합니다.",
        primaryLabel: "자동입력하고 중간저장",
        onPrimary: runMiddleSave,
        note: "제출 버튼은 누르지 않습니다.",
      };
    }

    if (candidateCount > 0) {
      return {
        progress: 46,
        progressLabel: `${candidateCount}개 후보 발견`,
        eyebrow: "지금 할 일",
        title: "입력할 값만 먼저 확인하세요",
        body: "검토가 필요한 항목은 표시해 뒀습니다. 문제가 없으면 한 번에 승인하고 다음 단계로 넘어갑니다.",
        primaryLabel: "검토 완료, 모두 승인",
        onPrimary: approveAll,
        note: riskyRows.length > 0 ? "주의 표시가 있는 항목은 실제 제출 전 다시 확인하세요." : "승인 전에는 홈택스 값을 바꾸지 않습니다.",
      };
    }

    return {
      progress: 18,
      progressLabel: "시작 전",
      eyebrow: "지금 할 일",
      title: "신고서만 불러오면 돼요",
      body: "홈택스 로그인은 사용자가 직접 완료합니다. Open Magi는 화면을 읽어 입력 후보를 만들고, 승인 전에는 아무것도 바꾸지 않습니다.",
      primaryLabel: "신고서 읽기 시작",
      onPrimary: readFiling,
      note: "비밀번호 저장 없음 · 최종 제출 없음",
    };
  })();

  return (
    <section id="tax-assistant" className="mx-auto w-full max-w-5xl scroll-mt-24 px-4 py-10 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-3xl text-center">
        <div className="mb-5 flex justify-center">
          <Badge variant="default" className="bg-white text-foreground">
            홈택스 로그인은 직접 · 승인한 값만 자동입력
          </Badge>
        </div>
        <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
          종소세 자동입력, 중간저장까지
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-secondary">
          지금 해야 할 일 하나만 보여드릴게요. 최종 제출, 납부, 환급계좌 확정은 자동화하지 않습니다.
        </p>
      </div>

      <div className="mx-auto mt-10 max-w-2xl rounded-lg border border-black/10 bg-white p-6 shadow-sm sm:p-8" aria-live="polite">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm font-semibold text-foreground">{task.progressLabel}</span>
          <span className="rounded-full border border-black/10 px-3 py-1 text-xs font-semibold text-secondary">
            비회원
          </span>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-black/[0.06]">
          <div
            className="h-full rounded-full bg-emerald-500 transition-all duration-300"
            style={{ width: `${task.progress}%` }}
          />
        </div>

        <div className="mt-8">
          <p className="text-sm font-semibold text-emerald-700">{task.eyebrow}</p>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground">
            {task.title}
          </h2>
          <p className="mt-4 text-base leading-7 text-secondary">{task.body}</p>
        </div>

        <Button
          type="button"
          variant="cta"
          size="lg"
          className="mt-8 w-full"
          onClick={task.onPrimary}
          disabled={task.primaryDisabled}
        >
          {task.primaryLabel}
        </Button>
        <p className="mt-3 text-center text-sm text-secondary">{task.note}</p>

        {candidateCount > 0 ? (
          <div className="mt-8 border-t border-black/10 pt-6">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold text-foreground">입력 후보</h3>
              <span className="text-sm text-secondary">{approvedCount}/{candidateCount} 승인</span>
            </div>
            <ul className="mt-4 divide-y divide-black/10">
              {session.inputPlan.map((row) => (
                <li key={row.id} className="py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-semibold text-foreground">{row.label}</p>
                      <p className="mt-1 text-sm text-secondary">{row.proposedValue}</p>
                    </div>
                    <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${
                      row.approved
                        ? "bg-emerald-500/10 text-emerald-700"
                        : "bg-black/[0.04] text-secondary"
                    }`}>
                      {row.approved ? "승인됨" : "검토"}
                    </span>
                  </div>
                  {row.riskFlag ? (
                    <p className="mt-2 text-sm text-amber-700">{row.riskFlag}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="mx-auto mt-5 max-w-2xl divide-y divide-black/10 rounded-lg border border-black/10 bg-white">
        <details className="group p-5">
          <summary className="cursor-pointer list-none text-sm font-semibold text-foreground">
            진행 내역
          </summary>
          <ul className="mt-4 space-y-2 text-sm text-secondary">
            {completedEvents.length === 0 ? (
              <li>아직 홈택스 화면을 읽기 전입니다.</li>
            ) : (
              completedEvents.map((event) => <li key={event.id}>{event.label}</li>)
            )}
          </ul>
        </details>
        <details className="group p-5">
          <summary className="cursor-pointer list-none text-sm font-semibold text-foreground">
            자동화 안전 경계
          </summary>
          <p className="mt-4 text-sm leading-6 text-secondary">
            홈택스 로그인은 사용자가 직접 완료하고, Open Magi는 승인된 값만 입력합니다.
            최종 제출, 납부, 환급계좌 확정은 사용자가 직접 확인하고 진행합니다.
          </p>
        </details>
      </div>
    </section>
  );
}
