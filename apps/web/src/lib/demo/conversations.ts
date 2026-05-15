export interface DemoMessage {
  role: "user" | "assistant";
  /** Markdown content */
  content: string;
  /** ms to wait before showing this message */
  delay: number;
  /** Thinking text shown before assistant response */
  thinking?: string;
  /** Meta line below assistant message */
  meta?: string;
}

export interface DemoChannel {
  name: string;
  category: string;
  templateLabel?: string;
  continuity?: {
    reads: string;
    returns: string;
    saves: string;
  };
  messages: DemoMessage[];
}

const CHANNELS: DemoChannel[] = [
  {
    name: "Q3 launch",
    category: "WORK",
    templateLabel: "Acme workspace · Q3 launch",
    continuity: {
      reads: "March memo, CRM notes, launch calendar",
      returns: "Handoff note for Jae",
      saves: "Updated launch state and owner gap",
    },
    messages: [
      {
        role: "user",
        content: "Jae is taking over Q3 launch tomorrow. What changed since the March memo?",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Acme Q3 launch state**\n\n" +
          "| Area | March memo | Current state | Why it matters |\n" +
          "|------|------------|---------------|----------------|\n" +
          "| Launch date | June 10 | June 24 | Security review became a launch gate |\n" +
          "| Owner | Strategy | Product + Sales Ops | Handoff needs both owners |\n" +
          "| Open question | Customer notice | Still unassigned | Blocks customer timeline |\n\n" +
          "I left Jae's starting point as the security checklist, customer-notice draft, and the owner gap.",
        delay: 2200,
        thinking:
          "Reading the March launch memo...\nComparing CRM notes and calendar changes...\nSeparating changed assumptions from stale context...",
        meta: "opus-4.7 · $0.010 · 2.4s",
      },
      {
        role: "user",
        content: "Add that to tomorrow's handoff note.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "Added to the handoff note:\n\n" +
          "- Revised date: June 24\n" +
          "- Source trail: March memo, CRM update, security review note\n" +
          "- Next owner: Jae\n" +
          "- Open item: customer notice owner\n\n" +
          "Tomorrow's brief will open from this state.",
        delay: 1500,
        meta: "sonnet-4.6 · $0.004 · 1.1s",
      },
    ],
  },
  {
    name: "Weekly brief",
    category: "WORK",
    templateLabel: "Acme workspace · weekly brief",
    continuity: {
      reads: "Last run, CRM, source docs",
      returns: "Friday ops brief",
      saves: "Retry note and next watchpoints",
    },
    messages: [
      {
        role: "user",
        content: "Make a launch brief for Friday ops meeting. Flag security review and customer notice blockers.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Friday launch brief**\n\n" +
          "| Step | Status | Evidence |\n" +
          "|------|--------|----------|\n" +
          "| Load launch state | Done | Q3 launch state loaded |\n" +
          "| Pull CRM notes | Recovered | CRM search returned 502 once, retry succeeded |\n" +
          "| Generate brief | Done | weekly-launch-brief.pdf created |\n" +
          "| Check file | Done | File opens and includes source appendix |\n" +
          "| Save state | Done | Retry note and watchpoints saved |\n\n" +
          "I marked security review and customer-notice owner as Friday's blockers.",
        delay: 2600,
        thinking:
          "Loading the current launch state...\nRetrying the CRM lookup...\nOpening the finished brief...",
        meta: "opus-4.7 · $0.015 · 3.6s",
      },
      {
        role: "user",
        content: "Send me the short version Mina can paste into the meeting doc.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "Prepared a 6-line meeting version:\n\n" +
          "- Launch date: June 24\n" +
          "- Blocker: security review\n" +
          "- Open owner: customer notice\n" +
          "- Vendor risk: Atlas redlines\n" +
          "- Decision needed Friday: customer notice owner\n" +
          "- Source: weekly-launch-brief.pdf",
        delay: 1500,
        meta: "sonnet-4.6 · $0.003 · 1.0s",
      },
    ],
  },
  {
    name: "Board memo",
    category: "WORK",
    templateLabel: "Acme workspace · board memo",
    continuity: {
      reads: "Launch brief, current assumptions, source appendix",
      returns: "Board memo file",
      saves: "File path, assumptions, open questions",
    },
    messages: [
      {
        role: "user",
        content: "Turn the launch brief into a one-page board memo and attach the number sources.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Board memo file**\n\n" +
          "| Check | Result |\n" +
          "|-------|--------|\n" +
          "| File generated | q3-board-memo.docx |\n" +
          "| File exists | Verified |\n" +
          "| File opens | Verified |\n" +
          "| Source appendix included | Verified |\n" +
          "| Saved for next draft | Path, assumptions, unresolved questions |\n\n" +
          "The memo uses the latest June 24 date and keeps the source trail beside the file for the next draft.",
        delay: 2300,
        thinking:
          "Drafting from current launch state...\nWriting the memo to the workspace...\nOpening the file and checking appendix before finalizing...",
        meta: "sonnet-4.6 · $0.006 · 1.7s",
      },
    ],
  },
  {
    name: "Vendor contract",
    category: "WORK",
    templateLabel: "Acme workspace · vendor contract",
    continuity: {
      reads: "Atlas contract, Acme policy, procurement note",
      returns: "Launch-risk clauses and procurement note",
      saves: "Redlines, owner, next negotiation note",
    },
    messages: [
      {
        role: "user",
        content: "Have the legal agent check Atlas clauses that could delay launch. I need a procurement note too.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Vendor contract review**\n\n" +
          "The legal agent flagged three items:\n\n" +
          "1. Liability cap is below Acme policy.\n" +
          "2. Data-processing exhibit is missing subprocessors.\n" +
          "3. Auto-renewal notice is only 15 days.\n\n" +
          "I wrote the procurement note with owner, negotiation wording, and a Thursday redline checkpoint.",
        delay: 2400,
        thinking:
          "Sending Atlas clauses and Acme policy to the legal agent...\nReading the risk notes...\nDrafting the procurement note...",
        meta: "opus-4.7 · $0.018 · 4.0s",
      },
      {
        role: "user",
        content: "Add that redline checkpoint to the launch checklist.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "Added to Q3 launch checklist:\n\n" +
          "- Atlas redlines due Thursday\n" +
          "- Owner: Mina\n" +
          "- Procurement note linked\n" +
          "- Risk summary attached to Friday ops brief",
        delay: 1500,
        meta: "sonnet-4.6 · $0.004 · 1.1s",
      },
    ],
  },
  {
    name: "Client packet",
    category: "EXECUTION",
    templateLabel: "Acme workspace · client packet",
    continuity: {
      reads: "Launch research, source docs, file requirements",
      returns: "Client packet and working files",
      saves: "Version state, assumptions, source notes",
    },
    messages: [
      {
        role: "user",
        content: "Make the client launch packet, and keep the working files for next week's update.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Client packet created**\n\n" +
          "- `launch-brief.pdf` — review copy\n" +
          "- `assumptions.xlsx` — working model\n" +
          "- `source-notes.md` — source trail and judgment notes\n\n" +
          "**Verified:** all three files exist and open.\n\n" +
          "**Saved for next week:** key assumptions, unresolved questions, and source list. The next version can start from these working files.",
        delay: 2100,
        thinking:
          "Turning research into deliverables...\nChecking file formats and access...\nSaving reusable version state...",
        meta: "sonnet-4.6 · $0.008 · 2.2s",
      },
      {
        role: "user",
        content: "Next week, start from those files and show only what changed.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "Saved. Next week I will compare new numbers against `assumptions.xlsx`, update `launch-brief.pdf`, and write the changes back into `source-notes.md`.",
        delay: 1300,
        meta: "sonnet-4.6 · $0.003 · 0.9s",
      },
    ],
  },
  {
    name: "Ops review",
    category: "EXECUTION",
    templateLabel: "Acme workspace · ops review",
    continuity: {
      reads: "Last review, new events, owners, blockers",
      returns: "Weekly delta",
      saves: "New watchpoints, owners, follow-up checks",
    },
    messages: [
      {
        role: "user",
        content: "Show only what changed since last week's ops review.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Acme weekly review**\n\n" +
          "| Area | Last run | This week | Update |\n" +
          "|------|----------|-----------|--------|\n" +
          "| Actions | 4 open | 1 closed, 3 open | Keep two owners, replace one owner |\n" +
          "| Watchpoints | 2 active | 1 escalated | Move vendor risk to decision queue |\n" +
          "| Blockers | 1 owner blocked | Owner changed | New follow-up due Friday |\n\n" +
          "Next week starts from the updated owner, revised watchpoint, and the follow-up check saved today.",
        delay: 2200,
        thinking:
          "Loading last week's review state...\nComparing new events and owner status...\nUpdating watchpoints for the next cadence...",
        meta: "sonnet-4.6 · $0.006 · 1.9s",
      },
    ],
  },
  {
    name: "Follow-ups",
    category: "MEMORY",
    templateLabel: "Acme workspace · follow-ups",
    continuity: {
      reads: "Meeting notes, decisions, owners, deadlines",
      returns: "Open actions and reminder draft",
      saves: "Decision history behind each task",
    },
    messages: [
      {
        role: "user",
        content: "Meeting is over. Give me the remaining launch follow-ups with owners and a reminder draft.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Open actions with decision source**\n\n" +
          "| Action | Source decision | Owner | Due |\n" +
          "|--------|-----------------|-------|-----|\n" +
          "| Vendor shortlist | Monday ops review | Mina | Friday |\n" +
          "| Budget revision | CFO sync | You | Wednesday |\n" +
          "| Security questionnaire | Customer call follow-up | Platform | Open |\n\n" +
          "Reminder draft is ready. Each line keeps the owner, due date, and source meeting together.",
        delay: 1800,
        thinking:
          "Scanning launch meeting notes...\nExtracting decisions, owners, and deadlines...\nMatching open actions to source decisions...",
        meta: "sonnet-4.6 · $0.004 · 1.3s",
      },
    ],
  },
];

const CHANNELS_KO: DemoChannel[] = [
  {
    name: "Q3 출시",
    category: "WORK",
    templateLabel: "Acme 워크스페이스 · Q3 출시",
    continuity: {
      reads: "3월 메모, CRM 기록, 출시 일정",
      returns: "재윤에게 넘길 인계 메모",
      saves: "업데이트된 출시 상태와 비어 있는 담당자",
    },
    messages: [
      {
        role: "user",
        content: "재윤이 내일부터 Q3 출시를 이어받아. 3월 메모 이후 뭐가 바뀌었는지 정리해줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Acme Q3 출시 상태**\n\n" +
          "| 항목 | 3월 메모 | 현재 상태 | 왜 중요한지 |\n" +
          "|------|----------|-----------|--------------|\n" +
          "| 출시일 | 6월 10일 | 6월 24일 | 보안 리뷰가 출시 조건으로 들어옴 |\n" +
          "| 담당 | 전략팀 | 프로덕트 + 세일즈옵스 | 인계 담당이 둘로 나뉨 |\n" +
          "| 남은 질문 | 고객 공지 | 아직 담당 미정 | 고객 일정 확정 전에 막힘 |\n\n" +
          "재윤의 시작점은 보안 체크리스트, 고객 공지 초안, 비어 있는 담당자입니다.",
        delay: 2200,
        thinking:
          "3월 출시 메모 확인 중...\nCRM 기록과 일정 변경 비교...\n바뀐 가정과 오래된 맥락 분리...",
        meta: "opus-4.7 · $0.010 · 2.4s",
      },
      {
        role: "user",
        content: "그 내용 내일 인계 메모에도 넣어줘.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "인계 메모에 반영했습니다.\n\n" +
          "- 수정된 출시일: 6월 24일\n" +
          "- 출처: 3월 메모, CRM 업데이트, 보안 리뷰 노트\n" +
          "- 다음 담당자: 재윤\n" +
          "- 열린 항목: 고객 공지 담당자\n\n" +
          "내일 브리프는 이 상태에서 바로 열립니다.",
        delay: 1500,
        meta: "sonnet-4.6 · $0.004 · 1.1s",
      },
    ],
  },
  {
    name: "주간 브리프",
    category: "WORK",
    templateLabel: "Acme 워크스페이스 · 주간 브리프",
    continuity: {
      reads: "지난 실행, CRM, 원자료",
      returns: "금요일 운영 회의 브리프",
      saves: "재시도 기록과 다음 체크포인트",
    },
    messages: [
      {
        role: "user",
        content: "금요일 운영 회의 전에 출시 브리프 뽑아줘. 보안 리뷰랑 고객 공지 쪽 막힌 게 있으면 표시해줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**금요일 출시 브리프**\n\n" +
          "| 단계 | 상태 | 근거 |\n" +
          "|------|------|------|\n" +
          "| 출시 상태 로드 | 완료 | Q3 출시 상태 불러옴 |\n" +
          "| CRM 기록 조회 | 복구 | 검색 API 502 발생, 재시도 성공 |\n" +
          "| 브리프 생성 | 완료 | weekly-launch-brief.pdf 생성 |\n" +
          "| 파일 확인 | 완료 | 파일 열림, 출처 부록 확인 |\n" +
          "| 상태 저장 | 완료 | 재시도 기록과 체크포인트 저장 |\n\n" +
          "보안 리뷰와 고객 공지 담당자를 금요일 회의의 막힌 항목으로 표시했습니다.",
        delay: 2600,
        thinking:
          "현재 출시 상태 확인 중...\nCRM 기록 재조회 중...\n완성된 브리프 파일 여는 중...",
        meta: "opus-4.7 · $0.015 · 3.6s",
      },
      {
        role: "user",
        content: "민아가 회의 문서에 붙일 수 있게 짧은 버전도 줘.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "회의 문서용 6줄 요약입니다.\n\n" +
          "- 출시일: 6월 24일\n" +
          "- 블로커: 보안 리뷰\n" +
          "- 열린 담당자: 고객 공지\n" +
          "- 벤더 리스크: Atlas 레드라인\n" +
          "- 금요일 결정: 고객 공지 담당자\n" +
          "- 출처: weekly-launch-brief.pdf",
        delay: 1500,
        meta: "sonnet-4.6 · $0.003 · 1.0s",
      },
    ],
  },
  {
    name: "이사회 메모",
    category: "WORK",
    templateLabel: "Acme 워크스페이스 · 이사회 메모",
    continuity: {
      reads: "출시 브리프, 현재 가정, 출처 부록",
      returns: "이사회 메모 파일",
      saves: "파일 경로, 가정, 남은 질문",
    },
    messages: [
      {
        role: "user",
        content: "방금 브리프를 이사회 공유용 1페이지 메모로 바꿔줘. 숫자 근거도 같이 붙여줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**이사회 메모 파일**\n\n" +
          "| 확인 항목 | 결과 |\n" +
          "|-----------|------|\n" +
          "| 파일 생성 | q3-board-memo.docx |\n" +
          "| 파일 존재 | 확인 |\n" +
          "| 파일 열림 | 확인 |\n" +
          "| 출처 부록 포함 | 확인 |\n" +
          "| 다음 초안용 저장 | 경로, 가정, 남은 질문 |\n\n" +
          "최신 6월 24일 일정으로 맞췄고, 다음 초안용 출처 기록도 파일 옆에 남겼습니다.",
        delay: 2300,
        thinking:
          "현재 출시 상태로 메모 작성 중...\n워크스페이스에 파일 저장...\n부록 포함 여부와 파일 열림 확인...",
        meta: "sonnet-4.6 · $0.006 · 1.7s",
      },
    ],
  },
  {
    name: "벤더 계약",
    category: "WORK",
    templateLabel: "Acme 워크스페이스 · 벤더 계약",
    continuity: {
      reads: "Atlas 계약서, Acme 정책, 구매 메모",
      returns: "출시 일정에 걸리는 조항과 구매팀 메모",
      saves: "수정 포인트, 담당자, 다음 협상 메모",
    },
    messages: [
      {
        role: "user",
        content: "Atlas 계약서에서 출시 일정에 걸릴 만한 조항만 법무 에이전트한테 확인받아줘. 구매팀에 보낼 메모도 필요해.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**벤더 계약 리뷰**\n\n" +
          "법무 에이전트가 세 가지를 짚었습니다.\n\n" +
          "1. 책임 한도가 Acme 정책보다 낮습니다.\n" +
          "2. 데이터 처리 부록에 하위 처리업체가 빠져 있습니다.\n" +
          "3. 자동 갱신 통지 기간이 15일뿐입니다.\n\n" +
          "구매팀에 보낼 메모에는 담당자, 협상 문구, 목요일 레드라인 체크포인트를 넣었습니다.",
        delay: 2400,
        thinking:
          "Atlas 조항과 Acme 정책을 법무 에이전트에 전달 중...\n리스크 메모 확인 중...\n구매팀 메모 작성 중...",
        meta: "opus-4.7 · $0.018 · 4.0s",
      },
      {
        role: "user",
        content: "레드라인 체크포인트도 출시 체크리스트에 넣어줘.",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "Q3 출시 체크리스트에 추가했습니다.\n\n" +
          "- Atlas 레드라인: 목요일까지\n" +
          "- 담당자: 민아\n" +
          "- 구매팀 메모 연결\n" +
          "- 금요일 운영 브리프에 리스크 요약 포함",
        delay: 1500,
        meta: "sonnet-4.6 · $0.004 · 1.1s",
      },
    ],
  },
  {
    name: "고객 패킷",
    category: "EXECUTION",
    templateLabel: "Acme 워크스페이스 · 고객 패킷",
    continuity: {
      reads: "출시 리서치, 원자료, 파일 형식 요구사항",
      returns: "고객용 패킷과 작업 파일",
      saves: "버전 상태, 가정, 출처 메모",
    },
    messages: [
      {
        role: "user",
        content: "고객한테 보낼 출시 패킷 만들어줘. 다음 주에 숫자 바뀌면 이어서 고칠 수 있게 작업 파일도 같이 챙겨줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**고객용 패킷을 만들었습니다**\n\n" +
          "- `launch-brief.pdf` — 검토용 브리프\n" +
          "- `assumptions.xlsx` — 작업 모델\n" +
          "- `source-notes.md` — 출처와 판단 근거\n\n" +
          "**확인:** 세 파일 모두 존재하고 열립니다.\n\n" +
          "**다음 주에 남긴 것:** 핵심 가정, 아직 열린 질문, 출처 목록. 다음 업데이트는 이 파일 상태에서 이어서 시작합니다.",
        delay: 2100,
        thinking:
          "리서치를 실제 산출물로 변환 중...\n파일 형식과 접근 가능 여부 확인...\n다음 버전용 상태 저장...",
        meta: "sonnet-4.6 · $0.008 · 2.2s",
      },
      {
        role: "user",
        content: "다음 주에는 이 파일에서 바로 이어서 바뀐 것만 보면 되지?",
        delay: 700,
      },
      {
        role: "assistant",
        content:
          "네. 다음 주에는 새 숫자를 `assumptions.xlsx`와 비교하고, `launch-brief.pdf`를 업데이트하고, 바뀐 가정은 `source-notes.md`에 남기면 됩니다.",
        delay: 1300,
        meta: "sonnet-4.6 · $0.003 · 0.9s",
      },
    ],
  },
  {
    name: "운영 리뷰",
    category: "EXECUTION",
    templateLabel: "Acme 워크스페이스 · 운영 리뷰",
    continuity: {
      reads: "지난 리뷰, 새 이벤트, 담당자, 블로커",
      returns: "이번 주 달라진 것",
      saves: "새 체크포인트, 담당자, 팔로업 확인",
    },
    messages: [
      {
        role: "user",
        content: "지난주 운영 리뷰 이후 바뀐 것만 보여줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**Acme 주간 리뷰**\n\n" +
          "| 영역 | 지난 실행 | 이번 주 | 업데이트 |\n" +
          "|------|-----------|---------|----------|\n" +
          "| 액션 | 4개 열림 | 1개 완료, 3개 열림 | 담당자 1명 변경 |\n" +
          "| 체크포인트 | 2개 활성 | 1개 결정 안건으로 이동 | 벤더 리스크 승격 |\n" +
          "| 블로커 | 담당자 1명 막힘 | 담당자 변경 | 금요일 팔로업 생성 |\n\n" +
          "다음 주 리뷰는 새 담당자, 수정된 체크포인트, 오늘 저장한 팔로업에서 시작합니다.",
        delay: 2200,
        thinking:
          "지난주 운영 상태 불러오는 중...\n새 이벤트와 담당자 상태 비교...\n다음 주 체크포인트 업데이트...",
        meta: "sonnet-4.6 · $0.006 · 1.9s",
      },
    ],
  },
  {
    name: "팔로업",
    category: "MEMORY",
    templateLabel: "Acme 워크스페이스 · 팔로업",
    continuity: {
      reads: "회의록, 결정, 담당자, 기한",
      returns: "열린 액션과 리마인더 초안",
      saves: "각 태스크 뒤의 결정 이력",
    },
    messages: [
      {
        role: "user",
        content: "오늘 회의 끝났으니 남은 출시 할 일과 담당자만 정리해서 리마인더 초안까지 만들어줘.",
        delay: 400,
      },
      {
        role: "assistant",
        content:
          "**열린 액션과 출처 결정**\n\n" +
          "| 액션 | 출처 결정 | 담당 | 기한 |\n" +
          "|------|-----------|------|------|\n" +
          "| 벤더 후보 정리 | 월요일 운영 회의 | 민아 | 금요일 |\n" +
          "| 예산안 수정 | CFO 싱크 | 본인 | 수요일 |\n" +
          "| 보안 설문지 | 고객 콜 후속 결정 | 플랫폼팀 | 미정 |\n\n" +
          "리마인더 초안까지 만들었습니다. 각 줄에는 담당자, 기한, 이 일이 나온 회의를 함께 넣었습니다.",
        delay: 1800,
        thinking:
          "출시 회의록 확인 중...\n결정, 담당자, 기한 추출...\n열린 액션을 출처 결정과 매칭...",
        meta: "sonnet-4.6 · $0.004 · 1.3s",
      },
    ],
  },
];

export function getDemoChannels(locale: string): DemoChannel[] {
  if (locale === "ko") return CHANNELS_KO;
  return CHANNELS;
}
