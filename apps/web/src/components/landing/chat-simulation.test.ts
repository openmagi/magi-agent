import { describe, expect, it } from "vitest";

import { getChatSimulationSnapshot } from "./chat-simulation";

describe("landing chat simulation storytelling", () => {
  it("keeps the hero demo aligned with the reliable workplace-agent headline", () => {
    const snapshot = getChatSimulationSnapshot("hero", "ko");
    const messages = JSON.stringify(snapshot.messages);

    expect(snapshot.activeChannel).toBe("주간 브리프");
    expect(messages).toContain("금요일 운영 회의");
    expect(messages).toContain("재시도");
    expect(messages).toContain("Acme");
    expect(messages).not.toContain("완료 전에");
    expect(messages).not.toContain("도구가 실패");
  });

  it("lets each capability card drive a matching Acme workstream instead of pattern labels", () => {
    const specialistSnapshot = getChatSimulationSnapshot("capabilities", "ko", undefined, 3);
    const artifactSnapshot = getChatSimulationSnapshot("usecases", "ko", 4);
    const specialistMessages = JSON.stringify(specialistSnapshot.messages);
    const artifactMessages = JSON.stringify(artifactSnapshot.messages);

    expect(specialistSnapshot.activeChannel).toBe("벤더 계약");
    expect(specialistMessages).toContain("법무 에이전트");
    expect(specialistMessages).toContain("구매팀에 보낼 메모");
    expect(specialistMessages).not.toContain("법무 답변");
    expect(specialistMessages).not.toContain("따로");
    expect(specialistMessages).not.toContain("업무 흐름");

    expect(artifactSnapshot.activeChannel).toBe("고객 패킷");
    expect(artifactMessages).toContain("다음 주");
    expect(artifactMessages).toContain("작업 파일");
    expect(artifactMessages).not.toContain("채팅 요약");
  });

  it("mirrors the selected English Acme workstream instead of taxonomy channels", () => {
    const snapshot = getChatSimulationSnapshot("usecases", "en", 5);

    expect(snapshot.activeChannel).toBe("Ops review");
    expect(snapshot.headerLabel).toBe("Ops review");
    expect(snapshot.channels.map((channel) => channel.name)).toEqual(
      expect.arrayContaining([
        "Q3 launch",
        "Weekly brief",
        "Board memo",
        "Vendor contract",
        "Client packet",
        "Ops review",
        "Follow-ups",
      ]),
    );
    expect(snapshot.channels.map((channel) => channel.name)).not.toEqual(
      expect.arrayContaining([
        "Information lifecycle",
        "Reliable execution",
        "Completion evidence",
        "Specialist handoff",
        "Finance",
        "Legal",
      ]),
    );
    expect(JSON.stringify(snapshot.messages)).toContain("Show only what changed since last week's ops review");
    expect(JSON.stringify(snapshot.messages)).toContain("Acme");
  });

  it("mirrors the selected Korean Acme workstream instead of taxonomy channels", () => {
    const snapshot = getChatSimulationSnapshot("usecases", "ko", 1);

    expect(snapshot.activeChannel).toBe("주간 브리프");
    expect(snapshot.headerLabel).toBe("주간 브리프");
    expect(snapshot.channels.map((channel) => channel.name)).toEqual(
      expect.arrayContaining([
        "Q3 출시",
        "주간 브리프",
        "이사회 메모",
        "벤더 계약",
        "고객 패킷",
        "운영 리뷰",
        "팔로업",
      ]),
    );
    expect(snapshot.channels.map((channel) => channel.name)).not.toEqual(
      expect.arrayContaining([
        "정보 생애주기",
        "신뢰 가능한 실행",
        "완료 검증",
        "전문가 인계",
        "금융",
        "법률",
      ]),
    );
    expect(JSON.stringify(snapshot.messages)).toContain("주간 브리프");
    expect(JSON.stringify(snapshot.messages)).toContain("재시도");
    expect(JSON.stringify(snapshot.messages)).not.toContain("완료 전에");
    expect(JSON.stringify(snapshot.messages)).not.toContain("도구가 실패");
  });
});
