"use client";

import { useState, useEffect, useRef } from "react";

export type ChatSection = "hero" | "capabilities" | "usecases" | "comparison" | "privacy" | "day1";

interface ChatSimulationProps {
  activeSection: ChatSection;
  activeUseCase?: number;
  activeCapability?: number;
  locale?: string;
}

interface ChatMessage {
  role: "user" | "agent" | "system";
  text: string;
  meta?: string; // e.g., "sonnet-4.6 · $0.003 · 1.2s"
  delay: number; // ms from section start
}

// ─── Section content by locale ───
// System messages + meta stay English. User/agent text localized.

type SectionMessages = {
  hero: ChatMessage[];
  capabilities: ChatMessage[][];
  comparison: ChatMessage[];
  privacy: ChatMessage[];
  day1: ChatMessage[];
  usecases: ChatMessage[][];
};

const CONTENT: Record<string, SectionMessages> = {
  en: {
    hero: [
      { role: "system", text: "Acme workspace · Q3 launch · latest state loaded", delay: 0 },
      { role: "user", text: "Make a launch brief for Friday ops meeting.", delay: 800 },
      { role: "agent", text: "Acme weekly brief is ready.\n\n- Used: March launch memo, CRM notes, security review, open owners\n- Noticed: CRM search returned 502 once; retry succeeded\n- Created: weekly-launch-brief.pdf\n- Checked: file opens and source appendix is included\n\nI left the updated date, security gate, and customer-notice owner for Friday's review.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.6s", delay: 1600 },
    ],
    capabilities: [
      [
        { role: "system", text: "Acme Q3 launch · March memo · new CRM notes", delay: 0 },
        { role: "user", text: "Jae is picking up Q3 launch tomorrow. What changed since the March memo?", delay: 700 },
        { role: "agent", text: "I found the March plan, two later decisions, and one owner change.\n\nChanged: launch moved from June 10 to June 24.\nReason: enterprise security review became a launch gate.\nStill open: customer-notice owner.\n\nI saved the current launch state and marked Jae's starting point as the security checklist plus customer-notice draft.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.4s", delay: 1500 },
        { role: "user", text: "Add that to tomorrow's handoff note.", delay: 3100 },
        { role: "agent", text: "Added. The handoff note now has the changed date, decision sources, open owner, and the two files Jae should open first.", meta: "sonnet-4.6 \u00b7 $0.003 \u00b7 1.0s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme weekly brief · files · CRM · retry policy", delay: 0 },
        { role: "user", text: "Make a launch brief for Friday ops meeting. Flag security review and customer notice blockers.", delay: 800 },
        { role: "agent", text: "Friday brief is ready.\n\n1. Loaded last week's launch state and new source docs.\n2. CRM search returned 502 once; retry succeeded.\n3. Created weekly-launch-brief.pdf.\n4. Opened the file and checked the source appendix.\n\nI also left watchpoints for security review, customer notice owner, and next week's review.", meta: "opus-4.7 \u00b7 $0.012 \u00b7 2.8s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme follow-ups · meetings · decisions · owners", delay: 0 },
        { role: "user", text: "Meeting is over. Give me the remaining launch follow-ups with owners and reminder drafts.", delay: 700 },
        { role: "agent", text: "Remaining follow-ups:\n\n1. Vendor shortlist — Mina, Friday\n2. Budget revision — you, Wednesday\n3. Security questionnaire — platform team, before customer call\n\nI drafted the reminders and kept the meeting decision behind each one, so the next brief can explain where the task came from.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.3s", delay: 1500 },
        { role: "user", text: "Send me the short version for Slack.", delay: 3100 },
        { role: "agent", text: "Drafted a three-line Slack note with owners, due dates, and the source meeting for each task.", meta: "sonnet-4.6 \u00b7 $0.003 \u00b7 1.0s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme vendor contract · legal agent · procurement follow-up", delay: 0 },
        { role: "user", text: "Have the legal agent check only the Atlas clauses that could delay launch. I also need a procurement note.", delay: 700 },
        { role: "agent", text: "Legal agent flagged three items:\n\n1. Liability cap is below Acme policy.\n2. Data-processing exhibit is missing subprocessors.\n3. Auto-renewal notice is only 15 days.\n\nI added a procurement note with owner, negotiation wording, and a Thursday redline checkpoint.", meta: "opus-4.7 \u00b7 $0.018 \u00b7 4.0s", delay: 1500 },
        { role: "user", text: "Put the redline checkpoint into the launch checklist too.", delay: 3200 },
        { role: "agent", text: "Added to the Q3 launch checklist: Atlas redlines due Thursday, owner Mina, risk summary linked to the procurement note.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.1s", delay: 4000 },
      ],
    ],
    comparison: [
      { role: "system", text: "Acme ops review · prior run state · current source changes", delay: 0 },
      { role: "user", text: "Show only what changed since last week's ops review.", delay: 800 },
      { role: "agent", text: "Three changes:\n\n1. Security review moved from watchpoint to launch blocker.\n2. Atlas redlines now have an owner and Thursday deadline.\n3. Customer notice is still unassigned.\n\nI updated next week's review state with the new blocker, owner, and open notice question.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.5s", delay: 1600 },
    ],
    privacy: [
      { role: "user", text: "How is my data protected?", delay: 0 },
      { role: "agent", text: "Your data runs in an isolated container. No other user can access it. API keys are encrypted with AES-256-GCM. Network policy blocks all cross-user traffic.", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.9s", delay: 800 },
      { role: "system", text: "\ud83d\udd12 E2EE active \u00b7 Isolated runtime \u00b7 GDPR compliant", delay: 2200 },
    ],
    day1: [
      { role: "system", text: "Setting up your team’s continuity agent...", delay: 0 },
      { role: "system", text: "\u2713 Work sources connected \u00b7 \u2713 Domain tools loaded \u00b7 \u2713 Lifecycle memory active", delay: 800 },
      { role: "system", text: "Your agent is live. 47 seconds.", delay: 1600 },
      { role: "agent", text: "I’m ready. Ask about a project, case, client, or decision. I’ll work from the context you already have and keep the next useful details for later.", meta: "sonnet-4.6 \u00b7 $0.001 \u00b7 0.6s", delay: 2200 },
    ],
    usecases: [
      [
        { role: "system", text: "Acme Q3 launch · source docs · decisions · open questions", delay: 0 },
        { role: "user", text: "Jae takes over Q3 launch tomorrow. What changed since the March memo?", delay: 800 },
        { role: "agent", text: "I found the March plan, two prior decisions, and three open questions.\n\nChanged: June 10 moved to June 24.\nWhy it matters: security review is now a launch gate.\nNext start: security checklist, customer-notice draft, and the owner gap.\n\nI saved the updated launch state and source trail for the handoff.", meta: "opus-4.7 · $0.010 · 2.4s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme weekly brief · files · CRM · retry policy", delay: 0 },
        { role: "user", text: "Make a launch brief for Friday ops meeting. Flag security review and customer notice blockers.", delay: 800 },
        { role: "agent", text: "Friday brief is ready.\n\n1. Retrieved the Acme launch packet.\n2. CRM search returned 502 once; retry succeeded.\n3. Created weekly-launch-brief.pdf.\n4. Opened the file and checked the source appendix.\n\nI saved the retry note and next week's watchpoints with the brief.", meta: "opus-4.7 · $0.015 · 3.6s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme board memo · artifact path · access check", delay: 0 },
        { role: "user", text: "Turn the launch brief into a one-page board memo and attach the number sources.", delay: 800 },
        { role: "agent", text: "Board memo is ready.\n\nCreated: q3-board-memo.docx\nChecked: file opens, source appendix is included, latest launch date is used\nSaved: memo path, assumptions, unresolved questions\n\nThe next draft can start from this memo and the attached source trail.", meta: "sonnet-4.6 · $0.006 · 1.7s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme vendor contract · legal agent · procurement follow-up", delay: 0 },
        { role: "user", text: "Have the legal agent check Atlas clauses that could delay launch. I need a procurement note too.", delay: 700 },
        { role: "agent", text: "Legal agent flagged three items:\n\n1. Liability cap is below Acme policy.\n2. Data-processing exhibit is missing subprocessors.\n3. Auto-renewal notice is only 15 days.\n\nI wrote the procurement note with owner, negotiation wording, and a Thursday redline checkpoint.", meta: "opus-4.7 · $0.018 · 4.0s", delay: 1500 },
        { role: "user", text: "Add that redline checkpoint to the launch checklist.", delay: 3100 },
        { role: "agent", text: "Added. Q3 launch checklist now has Atlas redlines due Thursday, owner Mina, and a link to the procurement note.", meta: "sonnet-4.6 · $0.004 · 1.1s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme client packet · brief · model · source trail", delay: 0 },
        { role: "user", text: "Make the client launch packet, and keep the working files for next week's update.", delay: 700 },
        { role: "agent", text: "Created the Acme packet:\n\n- launch-brief.pdf for review\n- assumptions.xlsx for the working model\n- source-notes.md for traceability\n\nI checked that each file opens and saved the version state.", meta: "sonnet-4.6 · $0.008 · 2.2s", delay: 1500 },
        { role: "user", text: "Next week, start from those files and show only what changed.", delay: 3100 },
        { role: "agent", text: "Saved. Next week's update will compare new numbers against assumptions.xlsx and write changes back into source-notes.md.", meta: "sonnet-4.6 · $0.004 · 1.0s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme ops review · last run · new data · updated loop", delay: 0 },
        { role: "user", text: "Show only what changed since last week's ops review.", delay: 800 },
        { role: "agent", text: "Acme weekly review:\n\nLast run: 4 open actions, 2 watchpoints, 1 blocked owner.\nChanged this week: one action closed, Atlas risk moved to decision, and the blocked owner changed.\nNext review: starts from the new owner, revised watchpoint, and today's follow-up check.", meta: "sonnet-4.6 · $0.006 · 1.9s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme follow-ups · meeting notes · owners · deadlines", delay: 0 },
        { role: "user", text: "Meeting is over. Give me the remaining launch follow-ups with owners and reminder draft.", delay: 800 },
        { role: "agent", text: "Remaining launch follow-ups:\n\n1. Vendor shortlist — Mina, due Friday.\n2. Budget revision — you, due Wednesday.\n3. Security questionnaire — platform team, before customer call.\n\nI drafted the reminders and saved the source meeting behind each task.", meta: "sonnet-4.6 · $0.004 · 1.3s", delay: 1600 },
      ],
    ],
  },
  ko: {
    hero: [
      { role: "system", text: "Acme 워크스페이스 · Q3 출시 · 최신 상태 로드", delay: 0 },
      { role: "user", text: "금요일 운영 회의 전에 Acme 출시 브리프 하나 뽑아줘.", delay: 800 },
      { role: "agent", text: "Acme 주간 브리프를 만들었습니다.\n\n- 참고: 3월 출시 메모, CRM 기록, 보안 리뷰, 열린 담당자\n- 이슈: CRM 조회가 한 번 끊겨 재시도했습니다\n- 파일: weekly-launch-brief.pdf\n- 확인: 파일 열림, 출처 부록 포함\n\n금요일 리뷰용으로 바뀐 출시일, 보안 게이트, 고객 공지 담당자까지 남겼습니다.", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.6s", delay: 1600 },
    ],
    capabilities: [
      [
        { role: "system", text: "Acme Q3 출시 · 3월 메모 · 새 CRM 기록", delay: 0 },
        { role: "user", text: "재윤이 내일부터 이어받아야 해서, 3월 메모 이후 Q3 출시 준비가 어디까지 왔는지 정리해줘.", delay: 700 },
        { role: "agent", text: "3월 계획서, 이후 결정 2건, 담당자 변경을 확인했습니다.\n\n바뀐 점: 출시일이 6월 10일에서 6월 24일로 밀렸습니다.\n이유: 엔터프라이즈 보안 리뷰가 출시 조건으로 들어왔습니다.\n남은 일: 고객 공지 담당자가 아직 비어 있습니다.\n\n현재 출시 상태를 저장했고, 재윤의 시작점은 보안 체크리스트와 고객 공지 초안으로 남겼습니다.", meta: "sonnet-4.6 · $0.004 · 1.4s", delay: 1500 },
        { role: "user", text: "그 내용 내일 인계 메모에도 넣어줘.", delay: 3100 },
        { role: "agent", text: "인계 메모에 반영했습니다. 바뀐 출시일, 판단 근거, 열린 담당자, 재윤이 먼저 열어볼 파일 2개까지 넣었습니다.", meta: "sonnet-4.6 · $0.003 · 1.0s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme 주간 브리프 · 파일 · CRM · 재시도 정책", delay: 0 },
        { role: "user", text: "금요일 운영 회의 전에 출시 브리프 뽑아줘. 보안 리뷰랑 고객 공지 쪽 막힌 게 있으면 표시해줘.", delay: 800 },
        { role: "agent", text: "금요일 브리프를 만들었습니다.\n\n1. 지난주 출시 상태와 새 원자료를 불러왔습니다.\n2. CRM 조회가 한 번 끊겨 재시도했습니다.\n3. weekly-launch-brief.pdf를 만들었습니다.\n4. 파일이 열리고 출처 부록이 들어간 것을 확인했습니다.\n\n보안 리뷰, 고객 공지 담당자, 다음 주 체크포인트도 함께 남겼습니다.", meta: "opus-4.7 · $0.012 · 2.8s", delay: 1600 },
      ],
      [
        { role: "system", text: "Acme 팔로업 · 회의록 · 결정 · 담당자", delay: 0 },
        { role: "user", text: "오늘 회의 끝났으니 남은 출시 할 일과 담당자만 정리하고, 리마인더 초안도 만들어줘.", delay: 700 },
        { role: "agent", text: "남은 팔로업입니다.\n\n1. 벤더 후보 정리 — 민아, 금요일까지\n2. 예산안 수정 — 본인, 수요일까지\n3. 보안 설문지 — 플랫폼팀, 고객 콜 전까지\n\n리마인더 초안에는 담당자, 기한, 이 일이 생긴 회의까지 붙였습니다.", meta: "sonnet-4.6 · $0.004 · 1.3s", delay: 1500 },
        { role: "user", text: "슬랙에 보낼 짧은 버전으로 줄여줘.", delay: 3100 },
        { role: "agent", text: "세 줄짜리 슬랙 초안으로 줄였습니다. 각 줄에 담당자, 기한, 출처 회의를 같이 넣었습니다.", meta: "sonnet-4.6 · $0.003 · 1.0s", delay: 3900 },
      ],
      [
        { role: "system", text: "Acme 벤더 계약 · 법무 에이전트 · 구매 팔로업", delay: 0 },
        { role: "user", text: "Atlas 계약서에서 출시 일정에 걸릴 만한 조항만 법무 에이전트한테 확인받아줘. 구매팀에 보낼 메모도 필요해.", delay: 700 },
        { role: "agent", text: "법무 에이전트가 세 가지를 짚었습니다.\n\n1. 책임 한도가 Acme 정책보다 낮습니다.\n2. 데이터 처리 부록에 하위 처리업체가 빠져 있습니다.\n3. 자동 갱신 통지 기간이 15일뿐입니다.\n\n구매팀에 보낼 메모에는 담당자, 협상 문구, 목요일 레드라인 체크포인트를 넣었습니다.", meta: "opus-4.7 · $0.018 · 4.0s", delay: 1500 },
        { role: "user", text: "레드라인 체크포인트도 출시 체크리스트에 넣어줘.", delay: 3200 },
        { role: "agent", text: "Q3 출시 체크리스트에 추가했습니다. Atlas 레드라인은 목요일까지, 담당자는 민아, 리스크 요약은 구매팀 메모에 연결했습니다.", meta: "sonnet-4.6 · $0.004 · 1.1s", delay: 4000 },
      ],
    ],
    comparison: [
      { role: "system", text: "Acme 운영 리뷰 · 지난 실행 상태 · 새 원자료 변화", delay: 0 },
      { role: "user", text: "지난주 운영 리뷰 이후 바뀐 것만 보여줘.", delay: 800 },
      { role: "agent", text: "세 가지가 바뀌었습니다.\n\n1. 보안 리뷰가 체크포인트에서 출시 블로커로 올라왔습니다.\n2. Atlas 레드라인에 담당자와 목요일 기한이 생겼습니다.\n3. 고객 공지 담당자는 아직 비어 있습니다.\n\n다음 리뷰용 상태에 새 블로커, 담당자, 열린 질문을 반영했습니다.", meta: "sonnet-4.6 · $0.004 · 1.5s", delay: 1600 },
    ],
    privacy: [
      { role: "user", text: "\ub0b4 \ub370\uc774\ud130\ub294 \uc5b4\ub5bb\uac8c \ubcf4\ud638\ub3fc?", delay: 0 },
      { role: "agent", text: "\uaca9\ub9ac\ub41c Kubernetes Pod\uc5d0\uc11c \uc2e4\ud589\ub418\uba70 \ub2e4\ub978 \uc720\uc800\uac00 \uc811\uadfc\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4. API \ud0a4\ub294 AES-256-GCM\uc73c\ub85c \uc554\ud638\ud654\ub418\uace0 \ub124\ud2b8\uc6cc\ud06c \uc815\ucc45\uc774 Pod \uac04 \ud1b5\uc2e0\uc744 \ucc28\ub2e8\ud569\ub2c8\ub2e4.", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.9s", delay: 800 },
      { role: "system", text: "\ud83d\udd12 E2EE \ud65c\uc131 \u00b7 Pod \uaca9\ub9ac \u00b7 GDPR/PIPA \uc900\uc218", delay: 2200 },
    ],
    day1: [
      { role: "system", text: "업무가 이어지는 에이전트를 설정 중...", delay: 0 },
      { role: "system", text: "✓ 업무 소스 연결 · ✓ 도메인 도구 로드 · ✓ 생애주기 메모리 활성화", delay: 800 },
      { role: "system", text: "47초 만에 에이전트 준비 완료", delay: 1600 },
      { role: "agent", text: "준비됐습니다. 프로젝트, 사건, 고객, 의사결정에 대해 물어보세요. 이미 가진 업무 맥락에서 이어서 일하고, 다음에 필요한 내용은 기억으로 남겨두겠습니다.", meta: "sonnet-4.6 \u00b7 $0.001 \u00b7 0.6s", delay: 2200 },
    ],
    usecases: [
      // Finance
      [
        { role: "system", text: "맥락: 포트폴리오 · 12종목 · 한은 기준금리", delay: 0 },
        { role: "user", text: "오늘 한국은행 기준금리 발표에 영향 받는 종목은?", delay: 800 },
        { role: "agent", text: "기준금리 3.25% 동결, 하반기 인하 시사. 종목별 금리 민감도:\n\n📈 국고채 10년 ETF (15%): +1.8% — 듀레이션 7.2년\n📈 맥쿼리인프라 (8%): +1.2% — 인프라 리츠\n📉 KB금융 (10%): -0.7% — 순이자마진 축소\n⚠️ MMF (15%): 인하 시 수익률 하락\n\n25bp 인하 시나리오 모델링해볼까요?", meta: "opus-4.7 · $0.014 · 3.2s", delay: 1600 },
      ],
      // Legal
      [
        { role: "system", text: "맥락: 로펌 · 14건 진행 중 · 답변서 접수", delay: 0 },
        { role: "user", text: "김철수 부당해고 사건 답변서를 4요건별로 우리 증거랑 대조해줘", delay: 800 },
        { role: "agent", text: "답변서 vs 우리 증거:\n\n| 주장 | 우리 증거 | 충돌 |\n|------|---------|------|\n| \"매출 30% 감소\" | 사업보고서: **10%** | 수치 불일치 |\n| \"희망퇴직 실시\" | 경위서: **\"공고 못 봤다\"** | 정면 상충 |\n| \"합리적 기준 선정\" | 회의록: **\"인사팀 위임\"** | 기준 미기재 |\n\n첨부서류 4건 중 3건 \"추후 제출 예정.\" 쟁점별 판례 검색할까요?", meta: "opus-4.7 · $0.018 · 4.1s", delay: 1600 },
      ],
      // Accounting
      [
        { role: "system", text: "맥락: 회계법인 · 한빛전자 · 3년치 감사 데이터", delay: 0 },
        { role: "user", text: "한빛전자 전기 이슈랑 올해 Q1 시산표 교차해줘", delay: 800 },
        { role: "agent", text: "전기 이슈 × 2026 Q1:\n\n| 전기 이슈 | 2026 Q1 |\n|----------|--------|\n| 매출 cutoff 혼용 | Q1 매출 +22% — 계절적 편중 상단 |\n| 재고평가 혼용 | 충당금 +33% — **급증** |\n| IT통제 SoD 미비 | Q2 개선 예정 |\n\n3월 마지막 주 출하일≠검수일 거래 3건, 합계 23억원.\n\n세금계산서 상세 확인할까요?", meta: "opus-4.7 · $0.014 · 3.8s", delay: 1600 },
      ],
      // Tax
      [
        { role: "system", text: "맥락: 세무사무소 · 세무조사 · 3년치 신고서", delay: 0 },
        { role: "user", text: "대성물산 세무조사 대응 — 접대비 3년치 뽑아주고 업종 평균이랑 비교해줘", delay: 800 },
        { role: "agent", text: "3년 교차 분석:\n\n| 항목 | 대성물산 (2025) | 제조업 평균 | 차이 |\n|------|--------------|----------|------|\n| 접대비/수입금액 | 1.4% | 0.8% | **+0.6%p** |\n| 접대비 증가율 | +21.7% | +5.2% | **+16.5%p** |\n\n2025년 접대비 한도 초과 3,200만원 손금불산입. 12월 급증 패턴.\n\n소명 관련 심판례 검색할까요?", meta: "opus-4.7 · $0.024 · 5.8s", delay: 1600 },
      ],
      // Restaurants
      [
        { role: "system", text: "맥락: 레스토랑 · POS 데이터 · 거래처 3곳", delay: 0 },
        { role: "user", text: "이번 주 식자재 원가가 왜 올랐어?", delay: 800 },
        { role: "agent", text: "이번 주 식자재 원가 12% 상승 (420만원 → 470만원):\n\n🚨 원인: 거래처 신선플러스 농산물 가격 +18%\n📉 영향 메뉴: 샐러드 (마진 -15%), 보울 (마진 -8%)\n\n대안 2곳:\n• 그린리프: kg당 2,300원 vs 2,850원 (주당 ~34만원 절감)\n• 팜다이렉트: kg당 2,150원, 익일 배송\n\n다음 발주에서 거래처 변경할까요?", meta: "sonnet-4.6 · $0.004 · 1.5s", delay: 1600 },
      ],
      // Sales
      [
        { role: "system", text: "맥락: 영업 · CRM · 넥스트테크 계정", delay: 0 },
        { role: "user", text: "2시 콜 전에 넥스트테크 계정 브리핑해줘", delay: 800 },
        { role: "agent", text: "**넥스트테크** — 엔터프라이즈 딜, 연 2.4억원\n\n📅 마지막 연락: 3/28 (기술이사 데모)\n🚧 반론: 보안 감사 대기 중\n👥 이해관계자: 김 부장 (챔피언), 이 상무 (블로커)\n\n💬 추천 대화 포인트:\n1. ISMS 인증서 공유 (이 상무 우려 해소)\n2. 2분기 예산 타임라인 확인\n3. 개발팀 파일럿 제안", meta: "sonnet-4.6 · $0.005 · 1.8s", delay: 1600 },
      ],
      // Assistant
      [
        { role: "system", text: "맥락: 임원 · 회의록 · 지난주 5건 회의", delay: 0 },
        { role: "user", text: "지난주 회의에서 남은 액션 아이템이 뭐야?", delay: 800 },
        { role: "agent", text: "지난주 미처리 항목 5건:\n\n1. ⭕ 2분기 예산 초안 — 4/4 마감 (본인)\n2. ⭕ ERP 이전 벤더 후보 — 4/3 마감 (운영팀)\n3. ⭕ 이사회 발표자료 수정 — 4/5 마감 (본인)\n4. ✅ 고객 온보딩 체크리스트 — 3/29 완료\n5. ⭕ 채용 계획 검토 — 인사팀 회신 대기\n\n2번, 5번 팔로업 이메일 작성할까요?", meta: "sonnet-4.6 · $0.003 · 1.1s", delay: 1600 },
      ],
    ],
  },
  ja: {
    hero: [
      { role: "system", text: "Agent online \u2014 7 skills loaded, smart router active", delay: 0 },
      { role: "user", text: "\u4f55\u304c\u3067\u304d\u308b\uff1f", delay: 800 },
      { role: "agent", text: "\u30ea\u30b5\u30fc\u30c1\u3001\u30ef\u30fc\u30af\u30d5\u30ed\u30fc\u81ea\u52d5\u5316\u3001\u30b9\u30b1\u30b8\u30e5\u30fc\u30eb\u7ba1\u7406\u3001\u5e02\u5834\u8ffd\u8de1\u3001\u30ec\u30dd\u30fc\u30c8\u4f5c\u6210\u306a\u3069\u3092\u81ea\u5f8b\u7684\u306b\u51e6\u7406\u3057\u307e\u3059\u300224/7\u7a3c\u50cd\u3057\u300115\u5206\u3054\u3068\u306b\u76ee\u6a19\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.8s", delay: 1600 },
    ],
    capabilities: [
      [
        { role: "user", text: "\u3053\u306eSEC\u5831\u544a\u3092\u524d\u56db\u534a\u671f\u3068\u6bd4\u8f03\u5206\u6790\u3057\u3066", delay: 0 },
        { role: "system", text: "Routing \u2192 opus-4.7 (sector: ANALYSIS)", delay: 400 },
        { role: "agent", text: "\u58f2\u4e0a QoQ 12%\u589e ($4.2B \u2192 $4.7B)\u3002\u55b6\u696d\u5229\u76ca\u7387 2.3pp\u6539\u5584\u3002\u6838\u5fc3: \u30af\u30e9\u30a6\u30c9 YoY 34%\u6210\u9577\u3002\u30ea\u30b9\u30af: \u5728\u5eab +18%\u3002", meta: "opus-4.7 \u00b7 $0.014 \u00b7 3.2s", delay: 1200 },
        { role: "user", text: "\u660e\u65e5\u306e\u5929\u6c17\u306f\uff1f", delay: 3000 },
        { role: "system", text: "Routing \u2192 gemini-3.1-pro (sector: CASUAL)", delay: 3400 },
        { role: "agent", text: "\u30bd\u30a6\u30eb\u660e\u65e5: 18\u00b0C\u3001\u66c7\u308a\u6642\u3005\u6674\u308c\u3002\u96e8\u306e\u5fc3\u914d\u306a\u3057\u3002\u304a\u51fa\u304b\u3051\u65e5\u548c\u3067\u3059\uff01", meta: "gemini-3.1-pro \u00b7 $0.0004 \u00b7 0.3s", delay: 3800 },
      ],
    ],
    comparison: [
      { role: "system", text: "6:00 AM \u2014 Heartbeat check", delay: 0 },
      { role: "agent", text: "\u304a\u306f\u3088\u3046\u3054\u3056\u3044\u307e\u3059\uff01\u4eca\u65e5\u306e\u30d6\u30ea\u30fc\u30d5\u30a3\u30f3\u30b0:\n\n\ud83d\udcca \u30dd\u30fc\u30c8\u30d5\u30a9\u30ea\u30aa: +2.3% (BTC $71,240)\n\ud83d\udce7 \u7dca\u6025\u30e1\u30fc\u30eb 3\u4ef6\n\ud83d\udcc5 \u4eca\u65e5\u306e\u4f1a\u8b70 2\u4ef6 (10\u6642, 15\u6642)\n\ud83d\udd14 JLPT\u30af\u30a4\u30ba\u6e96\u5099\u5b8c\u4e86", meta: "sonnet-4.6 \u00b7 $0.003 \u00b7 1.1s", delay: 600 },
      { role: "system", text: "\u30c1\u30e3\u30c3\u30c8\u30dc\u30c3\u30c8\u3068\u9055\u3044\u3001\u3042\u306a\u305f\u3092\u5f85\u3061\u307e\u305b\u3093\u3002\u5bdd\u3066\u3044\u308b\u9593\u306b4\u3064\u306e\u30c7\u30fc\u30bf\u3092\u78ba\u8a8d\u3057\u307e\u3057\u305f\u3002", delay: 2000 },
    ],
    privacy: [
      { role: "user", text: "\u30c7\u30fc\u30bf\u306f\u3069\u3046\u4fdd\u8b77\u3055\u308c\u3066\u308b\uff1f", delay: 0 },
      { role: "agent", text: "\u5206\u96e2\u3055\u308c\u305fKubernetes Pod\u3067\u5b9f\u884c\u3055\u308c\u3001\u4ed6\u306e\u30e6\u30fc\u30b6\u30fc\u306f\u30a2\u30af\u30bb\u30b9\u3067\u304d\u307e\u305b\u3093\u3002API\u30ad\u30fc\u306fAES-256-GCM\u3067\u6697\u53f7\u5316\u3002\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u30dd\u30ea\u30b7\u30fc\u304cPod\u9593\u901a\u4fe1\u3092\u30d6\u30ed\u30c3\u30af\u3002", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.9s", delay: 800 },
      { role: "system", text: "\ud83d\udd12 E2EE \u6709\u52b9 \u00b7 Pod \u5206\u96e2 \u00b7 GDPR \u6e96\u62e0", delay: 2200 },
    ],
    day1: [
      { role: "system", text: "Deploying your agent...", delay: 0 },
      { role: "system", text: "\u2713 Pod \u30d7\u30ed\u30d3\u30b8\u30e7\u30cb\u30f3\u30b0 \u00b7 \u2713 \u30b9\u30ad\u30eb\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb \u00b7 \u2713 \u30eb\u30fc\u30bf\u30fc\u30aa\u30f3\u30e9\u30a4\u30f3", delay: 800 },
      { role: "system", text: "47\u79d2\u3067\u30a8\u30fc\u30b8\u30a7\u30f3\u30c8\u30c7\u30d7\u30ed\u30a4\u5b8c\u4e86", delay: 1600 },
      { role: "agent", text: "\u3053\u3093\u306b\u3061\u306f\uff01Web\u691c\u7d22\u3001\u30c7\u30a3\u30fc\u30d7\u30ea\u30b5\u30fc\u30c1\u3001\u6587\u66f8\u5206\u6790\u30b9\u30ad\u30eb\u3092\u8a2d\u5b9a\u3057\u307e\u3057\u305f\u3002\u4f55\u304b\u3089\u59cb\u3081\u307e\u3057\u3087\u3046\u304b\uff1f", meta: "sonnet-4.6 \u00b7 $0.001 \u00b7 0.6s", delay: 2200 },
    ],
    usecases: [
      [{ role: "system", text: "Template: Marketing \u00b7 7 skills \u00b7 Google Ads connected", delay: 0 }, { role: "user", text: "\u4eca\u9031\u306eGoogle Ads\u30d1\u30d5\u30a9\u30fc\u30de\u30f3\u30b9\u3092\u5206\u6790\u3057\u3066", delay: 800 }, { role: "agent", text: "\u30ad\u30e3\u30f3\u30da\u30fc\u30f3\u5206\u6790\u5b8c\u4e86:\n\n\ud83d\udcc8 CTR 14%\u4e0a\u6607 (3.2% \u2192 3.7%)\n\ud83d\udcb0 CPA 8%\u6e1b\u5c11\n\ud83c\udfc6 \u6700\u9ad8\u6210\u7e3e: \"AI Agent\"\u30b0\u30eb\u30fc\u30d7\n\u26a0\ufe0f \u4f4e\u6210\u7e3e\u5e83\u544a2\u4ef6\u505c\u6b62\u3001$50/\u65e5\u518d\u914d\u5206\u3002", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.8s", delay: 1600 }],
      [{ role: "system", text: "Template: Language Tutor \u00b7 JLPT N3 \u00b7 Daily 8 AM", delay: 0 }, { role: "agent", text: "\u304a\u306f\u3088\u3046\uff01\u4eca\u65e5\u306e\u30ec\u30c3\u30b9\u30f3: \u3066-form \ud83c\uddef\ud83c\uddf5\n\n\u98df\u3079\u308b \u2192 \u98df\u3079\u3066\n\u98f2\u3080 \u2192 \u98f2\u3093\u3067\n\n\u30af\u30a4\u30ba: \u8aad\u3080\u306e\u3066-form\u306f\uff1f", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.7s", delay: 800 }, { role: "user", text: "\u8aad\u3093\u3067\uff1f", delay: 2200 }, { role: "agent", text: "\u6b63\u89e3\uff01\u4eca\u9031 8/10 \u2014 \u5148\u9031\u306e5/10\u304b\u3089\u4e0a\u6607\uff01\ud83d\udcc8", meta: "haiku-4.5 \u00b7 $0.0003 \u00b7 0.2s", delay: 2800 }],
      [{ role: "system", text: "Template: Work Assistant \u00b7 Notion + Slack", delay: 0 }, { role: "agent", text: "\u30e2\u30fc\u30cb\u30f3\u30b0\u30d6\u30ea\u30fc\u30d5\u30a3\u30f3\u30b0:\n\n\ud83d\udce7 \u7dca\u6025\u30e1\u30fc\u30eb 2\u4ef6\n\ud83d\udccb Notion\u30bf\u30b9\u30af 3\u4ef6\n\ud83d\udcac Slack: 12\u672a\u8aad\n\n\u8fd4\u4fe1\u8349\u7a3f\u3092\u4f5c\u6210\u3057\u307e\u3057\u305f\u3002\u78ba\u8a8d\u3057\u307e\u3059\u304b\uff1f", meta: "sonnet-4.6 \u00b7 $0.003 \u00b7 1.2s", delay: 800 }, { role: "user", text: "\u898b\u305b\u3066", delay: 2400 }, { role: "agent", text: "\u8349\u7a3f1: \u91d1\u66dc\u65e5\u7d0d\u54c1\u78ba\u8a8d + Notion\u9032\u6357\u3002\n\u8349\u7a3f2: Notion\u9031\u6b21\u30bf\u30b9\u30af\u8981\u7d04\u3002\n\n\u627f\u8a8d\u3001\u7de8\u96c6\u3001\u307e\u305f\u306f\u7834\u68c4\uff1f", meta: "sonnet-4.6 \u00b7 $0.004 \u00b7 1.5s", delay: 3000 }],
      [{ role: "system", text: "Template: Personal \u00b7 Calendar + Weather", delay: 0 }, { role: "agent", text: "\u304a\u306f\u3088\u3046\uff01\u2600\ufe0f\n\n\ud83c\udf24 \u30bd\u30a6\u30eb: 22\u00b0C\u3001\u6674\u308c\n\ud83d\udcc5 14\u6642\u6b6f\u79d1 (\u6c5f\u5357\u99c5)\n\ud83d\ude87 13:20\u51fa\u767a\n\ud83d\udca1 \u304a\u6bcd\u3055\u3093\u306e\u8a95\u751f\u65e53\u65e5\u5f8c", meta: "sonnet-4.6 \u00b7 $0.002 \u00b7 0.9s", delay: 800 }, { role: "user", text: "\u6bcd\u306e\u8a95\u751f\u65e5\u30d7\u30ec\u30bc\u30f3\u30c85000\u5186\u4ee5\u4e0b\u3067\u63a2\u3057\u3066", delay: 2200 }, { role: "agent", text: "3\u3064\u306e\u63d0\u6848:\n\n1. \ud83c\udf38 \u30d7\u30ec\u30df\u30a2\u30e0\u8336\u30bb\u30c3\u30c8 (\u00a53,800)\n2. \ud83d\udcda \u30d9\u30b9\u30c8\u30bb\u30e9\u30fc\u30bb\u30c3\u30c8 (\u00a54,200)\n3. \ud83e\uddf4 \u30b9\u30ad\u30f3\u30b1\u30a2\u30bb\u30c3\u30c8 (\u00a54,500)\n\n\u6ce8\u6587\u3057\u307e\u3059\u304b\uff1f", meta: "sonnet-4.6 \u00b7 $0.005 \u00b7 2.1s \u00b7 web-search", delay: 2800 }],
      [{ role: "system", text: "Template: Researcher \u00b7 opus-4.7 \u00b7 6-phase", delay: 0 }, { role: "user", text: "2026\u5e74AI\u30a8\u30fc\u30b8\u30a7\u30f3\u30c8\u5e02\u5834\u306e\u30c7\u30a3\u30fc\u30d7\u30ea\u30b5\u30fc\u30c1", delay: 600 }, { role: "system", text: "SCOPE \u2192 SEARCH \u2192 FILTER \u2192 ANALYZE \u2192 SYNTHESIZE \u2192 DELIVER", delay: 1000 }, { role: "agent", text: "47\u30bd\u30fc\u30b9\u5206\u6790\u5b8c\u4e86:\n\n\u5e02\u5834: $18.2B (\u2191340% YoY)\u3002\u4f01\u696d42%\u3001\u500b\u4eba28%\u3001\u30d0\u30fc\u30c6\u30a3\u30ab\u30eb30%\u3002Open Magi\u306e\u307fx402\u81ea\u5f8b\u6c7a\u6e08\u63d0\u4f9b\u3002\n\n12\u30da\u30fc\u30b8\u30ec\u30dd\u30fc\u30c8\u3092\u30ef\u30fc\u30af\u30b9\u30da\u30fc\u30b9\u306b\u9001\u4fe1\u3002", meta: "opus-4.7 \u00b7 $0.089 \u00b7 12min \u00b7 deep-research", delay: 2400 }],
      [{ role: "system", text: "Template: Seoul Life \u00b7 Law + Auction + Restaurant", delay: 0 }, { role: "user", text: "\u30bd\u30a6\u30eb\u6c5f\u5357\u533a\u306e\u30de\u30f3\u30b7\u30e7\u30f3\u7af6\u58f2\u7269\u4ef6\u3092\u691c\u7d22\u3057\u3066", delay: 800 }, { role: "agent", text: "\u6c5f\u5357\u533a\u7af6\u58f2 3\u4ef6:\n\n1. \ud83c\udfe0 \u99c5\u4e09\u6d1e \u30e9\u30df\u30a2\u30f3 32\u5e73 \u2014 \u6700\u4f4e\u4fa1 12.6\u5104 (70%)\n2. \ud83c\udfe0 \u5927\u5cf8\u6d1e \u30a6\u30f3\u30de 25\u5e73 \u2014 \u6700\u4f4e\u4fa1 17.6\u5104 (80%)\n3. \ud83c\udfe0 \u8ad6\u73fe\u6d1e 15\u5e73 \u2014 \u6700\u4f4e\u4fa1 2.9\u5104 (69%)\n\n\u5165\u672d\u65e5: 4/15\u3002\u8a73\u7d30\u5206\u6790\u3057\u307e\u3059\u304b\uff1f", meta: "sonnet-4.6 \u00b7 $0.006 \u00b7 2.3s \u00b7 court-auction", delay: 1600 }],
    ],
  },
};

// zh and es fall back to en
const TRANSFORMATION_USECASES_KO: ChatMessage[][] = [
  [
    { role: "system", text: "Acme Q3 출시 · 원자료 · 결정 · 남은 질문", delay: 0 },
    { role: "user", text: "재윤이 내일부터 Q3 출시를 이어받아. 3월 메모 이후 뭐가 바뀌었는지 정리해줘.", delay: 800 },
    { role: "agent", text: "3월 계획서, 이전 결정 2건, 아직 열린 질문 3개를 확인했습니다.\n\n바뀐 점: 출시일이 6월 10일에서 6월 24일로 밀렸습니다.\n이유: 보안 리뷰가 출시 조건으로 들어왔습니다.\n재윤의 시작점: 보안 체크리스트, 고객 공지 초안, 비어 있는 담당자.\n\n현재 출시 상태와 출처를 인계 메모에 남겼습니다.", meta: "opus-4.7 · $0.010 · 2.4s", delay: 1600 },
  ],
  [
    { role: "system", text: "Acme 주간 브리프 · 파일 · CRM · 재시도 정책", delay: 0 },
    { role: "user", text: "금요일 운영 회의 전에 출시 브리프 뽑아줘. 보안 리뷰랑 고객 공지 쪽 막힌 게 있으면 표시해줘.", delay: 800 },
    { role: "agent", text: "금요일 브리프를 만들었습니다.\n\n1. Acme 출시 자료 묶음을 찾았습니다.\n2. CRM 검색 API 502가 한 번 떠서 재시도했습니다.\n3. weekly-launch-brief.pdf를 만들었습니다.\n4. 파일이 열리고 출처 부록이 들어간 것을 확인했습니다.\n\n재시도 기록과 다음 주 체크포인트도 브리프 상태에 남겼습니다.", meta: "opus-4.7 · $0.015 · 3.6s", delay: 1600 },
  ],
  [
    { role: "system", text: "Acme 이사회 메모 · 파일 경로 · 접근 확인", delay: 0 },
    { role: "user", text: "방금 브리프를 이사회 공유용 1페이지 메모로 바꿔줘. 숫자 근거도 같이 붙여줘.", delay: 800 },
    { role: "agent", text: "이사회 메모를 만들었습니다.\n\n생성: q3-board-memo.docx\n확인: 파일 열림, 출처 부록 포함, 최신 출시일 반영\n저장: 파일 위치, 가정, 남은 질문\n\n다음 초안은 이 메모와 출처 기록에서 이어서 작업할 수 있습니다.", meta: "sonnet-4.6 · $0.006 · 1.7s", delay: 1600 },
  ],
  [
    { role: "system", text: "Acme 벤더 계약 · 법무 에이전트 · 구매 팔로업", delay: 0 },
    { role: "user", text: "Atlas 계약서에서 출시 일정에 걸릴 만한 조항만 법무 에이전트한테 확인받아줘. 구매팀에 보낼 메모도 필요해.", delay: 700 },
    { role: "agent", text: "법무 에이전트가 세 가지를 짚었습니다.\n\n1. 책임 한도가 Acme 정책보다 낮습니다.\n2. 데이터 처리 부록에 하위 처리업체가 빠져 있습니다.\n3. 자동 갱신 통지 기간이 15일뿐입니다.\n\n구매팀에 보낼 메모에는 담당자, 협상 문구, 목요일 레드라인 체크포인트를 넣었습니다.", meta: "opus-4.7 · $0.018 · 4.0s", delay: 1500 },
    { role: "user", text: "레드라인 체크포인트도 출시 체크리스트에 넣어줘.", delay: 3100 },
    { role: "agent", text: "Q3 출시 체크리스트에 추가했습니다. Atlas 레드라인은 목요일까지, 담당자는 민아, 리스크 요약은 구매팀 메모에 연결했습니다.", meta: "sonnet-4.6 · $0.004 · 1.1s", delay: 3900 },
  ],
  [
    { role: "system", text: "Acme 고객 패킷 · 브리프 · 모델 · 출처 기록", delay: 0 },
    { role: "user", text: "고객한테 보낼 출시 패킷 만들어줘. 다음 주에 숫자 바뀌면 이어서 고칠 수 있게 작업 파일도 같이 챙겨줘.", delay: 700 },
    { role: "agent", text: "Acme 고객에게 보낼 패킷을 만들었습니다.\n\n- launch-brief.pdf: 검토용 브리프\n- assumptions.xlsx: 작업 모델\n- source-notes.md: 출처와 판단 근거\n\n각 파일이 열리는지 확인했고, 이번 버전의 가정도 저장했습니다.", meta: "sonnet-4.6 · $0.008 · 2.2s", delay: 1500 },
    { role: "user", text: "다음 주에는 이 파일에서 바로 이어서 바뀐 것만 보면 되지?", delay: 3100 },
    { role: "agent", text: "네. 파일 경로, 가정, 출처 메모, 아직 열린 질문을 함께 남겼습니다. 다음 주에는 새 숫자를 assumptions.xlsx와 비교하고 source-notes.md에 변경점을 남기면 됩니다.", meta: "sonnet-4.6 · $0.004 · 1.0s", delay: 3900 },
  ],
  [
    { role: "system", text: "Acme 운영 리뷰 · 지난 실행 · 새 데이터 · 업데이트된 상태", delay: 0 },
    { role: "user", text: "지난주 운영 리뷰 이후 바뀐 것만 보여줘.", delay: 800 },
    { role: "agent", text: "Acme 주간 리뷰입니다.\n\n지난 실행: 열린 액션 4개, 체크포인트 2개, 막힌 담당자 1명.\n이번 주 변화: 액션 1개 완료, 벤더 리스크 1건이 결정 안건으로 이동, 막힌 항목의 담당자 변경.\n다음 리뷰: 새 담당자, 수정된 체크포인트, 오늘 남긴 팔로업에서 시작합니다.", meta: "sonnet-4.6 · $0.006 · 1.9s", delay: 1600 },
  ],
  [
    { role: "system", text: "Acme 팔로업 · 회의록 · 담당자 · 기한", delay: 0 },
    { role: "user", text: "오늘 회의 끝났으니 남은 출시 할 일과 담당자만 정리해서 리마인더 초안까지 만들어줘.", delay: 800 },
    { role: "agent", text: "남은 팔로업입니다.\n\n1. 벤더 후보 정리 — 민아, 금요일까지.\n2. 예산안 수정 — 본인, 수요일까지.\n3. 보안 설문지 — 플랫폼팀, 고객 콜 전까지.\n\n리마인더 초안을 만들고, 각 태스크가 나온 회의도 함께 남겼습니다.", meta: "sonnet-4.6 · $0.004 · 1.3s", delay: 1600 },
  ],
];

function getLocaleContent(locale: string): SectionMessages {
  return CONTENT[locale] ?? CONTENT.en;
}

function getMessages(
  section: ChatSection,
  locale: string,
  useCaseIdx?: number,
  capabilityIdx?: number,
): ChatMessage[] {
  const c = getLocaleContent(locale);
  if (section === "capabilities") {
    return c.capabilities[capabilityIdx ?? 1] ?? c.capabilities[0];
  }
  if (section === "usecases") {
    const usecases = locale === "ko" ? TRANSFORMATION_USECASES_KO : c.usecases;
    return usecases[useCaseIdx ?? 0] ?? usecases[0];
  }
  return c[section];
}

// ─── Channel mapping ───

interface Channel {
  name: string;
  category: string;
}

const CHANNELS_BY_LOCALE: Record<"en" | "ko", Channel[]> = {
  en: [
    { name: "Q3 launch", category: "ACME WORK" },
    { name: "Weekly brief", category: "ACME WORK" },
    { name: "Board memo", category: "ACME WORK" },
    { name: "Vendor contract", category: "ACME WORK" },
    { name: "Client packet", category: "EXECUTION" },
    { name: "Ops review", category: "EXECUTION" },
    { name: "Follow-ups", category: "MEMORY" },
    { name: "Security", category: "SYSTEM" },
  ],
  ko: [
    { name: "Q3 출시", category: "Acme 업무" },
    { name: "주간 브리프", category: "Acme 업무" },
    { name: "이사회 메모", category: "Acme 업무" },
    { name: "벤더 계약", category: "Acme 업무" },
    { name: "고객 패킷", category: "실행" },
    { name: "운영 리뷰", category: "실행" },
    { name: "팔로업", category: "기록" },
    { name: "보안", category: "시스템" },
  ],
};

function getLocaleKey(locale: string): "en" | "ko" {
  return locale === "ko" ? "ko" : "en";
}

function getChannels(locale: string): Channel[] {
  return CHANNELS_BY_LOCALE[getLocaleKey(locale)];
}

function getActiveChannel(section: ChatSection, locale: string, useCaseIdx?: number, capabilityIdx?: number): string {
  const channels = getChannels(locale);
  const capabilityChannelIndexes = [0, 1, 6, 3];
  switch (section) {
    case "hero": return channels[1]?.name ?? "";
    case "capabilities": return channels[capabilityChannelIndexes[capabilityIdx ?? 1] ?? 1]?.name ?? "";
    case "comparison": return channels[4]?.name ?? "";
    case "privacy": return channels.find((channel) => channel.category === "SYSTEM" || channel.category === "시스템")?.name ?? channels[0]?.name ?? "";
    case "day1": return channels[6]?.name ?? "";
    case "usecases": return channels[useCaseIdx ?? 0]?.name ?? channels[0]?.name ?? "";
  }
}

export function getChatSimulationSnapshot(
  section: ChatSection,
  locale = "en",
  useCaseIdx?: number,
  capabilityIdx?: number,
): {
  messages: ChatMessage[];
  channels: Channel[];
  activeChannel: string;
  headerLabel: string;
} {
  const channels = getChannels(locale);
  const activeChannel = getActiveChannel(section, locale, useCaseIdx, capabilityIdx);

  return {
    messages: getMessages(section, locale, useCaseIdx, capabilityIdx),
    channels,
    activeChannel,
    headerLabel: activeChannel,
  };
}

// ─── Component ───

export function ChatSimulation({ activeSection, activeUseCase, activeCapability, locale }: ChatSimulationProps) {
  const [visibleCount, setVisibleCount] = useState(0);
  const [typingIdx, setTypingIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const snapshot = getChatSimulationSnapshot(activeSection, locale ?? "en", activeUseCase, activeCapability);
  const { messages, channels, activeChannel } = snapshot;

  // Reset and animate on section/usecase change
  useEffect(() => {
    // Clear previous timers
    for (const t of timerRef.current) clearTimeout(t);
    timerRef.current = [];
    setVisibleCount(0);
    setTypingIdx(null);

    // Schedule each message
    messages.forEach((msg, i) => {
      // Show typing indicator before agent messages
      if (msg.role === "agent") {
        const typingTimer = setTimeout(() => setTypingIdx(i), Math.max(0, msg.delay - 400));
        timerRef.current.push(typingTimer);
      }

      const showTimer = setTimeout(() => {
        setVisibleCount(i + 1);
        setTypingIdx((current) => current === i ? null : current);
      }, msg.delay + (msg.role === "agent" ? 600 : 0));
      timerRef.current.push(showTimer);
    });

    return () => {
      for (const t of timerRef.current) clearTimeout(t);
    };
  }, [activeSection, activeUseCase, activeCapability, locale, messages]);

  // Auto-scroll to bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visibleCount, typingIdx]);

  const visibleMessages = messages.slice(0, visibleCount);
  const isTyping = typingIdx !== null && typingIdx >= visibleCount;

  // Group channels by category
  const categories = [...new Set(channels.map((c) => c.category))];

  return (
    <div className="w-full rounded-2xl border border-black/[0.08] bg-white shadow-lg overflow-hidden flex" style={{ height: 520 }}>
      <div className="w-[210px] shrink-0 border-r border-black/[0.06] bg-white flex flex-col">
        <div className="p-4 border-b border-black/[0.06]">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary to-cta flex items-center justify-center">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-foreground truncate">work-agent</div>
              <div className="text-[11px] text-emerald-600 flex items-center gap-1.5 mt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
                active
              </div>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2 px-2">
          {categories.map((cat) => (
            <div key={cat} className="mb-2">
              <div className="px-2 py-1 text-[10px] font-semibold text-secondary/50 uppercase tracking-wider">{cat}</div>
              {channels.filter((c) => c.category === cat).map((ch) => (
                <div
                  key={ch.name}
                  className={`flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-xs leading-snug transition-colors ${
                    ch.name === activeChannel
                      ? "bg-primary/8 text-foreground font-medium"
                      : "text-secondary"
                  }`}
                >
                  <span className="text-secondary/40">#</span>
                  <span className="min-w-0 truncate">{ch.name}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-3 border-b border-black/[0.06] flex items-center gap-2">
          <span className="text-base font-semibold text-foreground truncate"># {activeChannel}</span>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto overflow-x-hidden px-5 py-5">
          <div className="max-w-3xl mx-auto">
            {visibleMessages.map((msg, i) => (
              <div key={`${activeSection}-${activeUseCase}-${activeCapability}-${i}`} className="animate-[chat-message-in_0.25s_ease-out]">
                {msg.role === "system" ? (
                  <div className="my-4 flex items-center gap-3">
                    <div className="h-px flex-1 bg-black/[0.06]" />
                    <span className="max-w-[72%] truncate text-[11px] text-secondary/50">
                      {msg.text}
                    </span>
                    <div className="h-px flex-1 bg-black/[0.06]" />
                  </div>
                ) : msg.role === "user" ? (
                  <div className="mb-4 flex justify-end">
                    <div className="max-w-[75%] rounded-2xl rounded-br-md bg-black/[0.04] px-4 py-2.5 text-sm text-foreground whitespace-pre-wrap leading-relaxed break-words">
                      {msg.text}
                    </div>
                  </div>
                ) : (
                  <div className="mb-5 flex justify-start">
                    <div className="w-full min-w-0 max-w-full overflow-hidden py-1 text-foreground">
                      <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                        {msg.text}
                      </div>
                      {msg.meta && (
                        <div className="mt-1">
                          <span className="text-[10px] text-secondary/30 font-mono">{msg.meta}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isTyping && (
              <div className="mb-5 flex justify-start animate-[chat-message-in_0.25s_ease-out]">
                <div className="flex items-center gap-1 py-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-secondary/40 animate-[chat-pulse_1.2s_ease-in-out_infinite]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-secondary/40 animate-[chat-pulse_1.2s_ease-in-out_infinite]" style={{ animationDelay: "0.2s" }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-secondary/40 animate-[chat-pulse_1.2s_ease-in-out_infinite]" style={{ animationDelay: "0.4s" }} />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="px-5 pb-4 pt-2">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-black/[0.04] text-secondary/60">
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94a3 3 0 014.243 4.243L8.552 18.32a1.5 1.5 0 01-2.121-2.122l9.192-9.193" />
              </svg>
            </div>
            <div className="flex min-h-10 flex-1 items-center rounded-2xl border border-black/[0.08] bg-black/[0.04] px-4 py-2 text-sm text-secondary/50">
              Message...
            </div>
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[#7C3AED] text-white shadow-[0_10px_28px_rgba(124,58,237,0.28)]">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18" />
              </svg>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
