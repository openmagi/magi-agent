"use client";

import { useRef, useEffect } from "react";
import { prepareWithSegments, layoutWithLines } from "@chenglou/pretext";
import { useCanvas, THEME } from "./use-canvas";

interface Message {
  role: "user" | "agent";
  text: string;
}

interface TerminalTypingProps {
  locale?: string;
}

const MESSAGES_BY_LOCALE: Record<string, Message[]> = {
  en: [
    { role: "user", text: "Schedule a team standup every morning at 9am" },
    { role: "agent", text: "Done. Recurring standup created for 9:00 AM KST, Mon-Fri. I'll send a reminder 5 minutes before." },
    { role: "user", text: "Research competitors in the AI agent space" },
    { role: "agent", text: "Deep research initiated. Analyzing 47 companies across 3 verticals. ETA: 12 min. Report will be sent to Telegram." },
    { role: "user", text: "Check my portfolio and rebalance if needed" },
    { role: "agent", text: "Portfolio scanned. BTC allocation drifted +4.2% above target. Submitting rebalance via KyberSwap. Confirm in wallet." },
  ],
  ko: [
    { role: "user", text: "매일 아침 9시에 팀 스탠드업 잡아줘" },
    { role: "agent", text: "완료. 월-금 오전 9시 KST 반복 스탠드업 생성. 5분 전 리마인더 보내드릴게요." },
    { role: "user", text: "AI 에이전트 경쟁사 조사해줘" },
    { role: "agent", text: "딥리서치 시작. 3개 분야 47개 기업 분석 중. 예상 12분. 완료되면 텔레그램으로 보고서 전송할게요." },
    { role: "user", text: "이번 달 지출 분석해서 보고해줘" },
    { role: "agent", text: "카드 내역 분석 완료. 식비 32% (+8% MoM), 교통비 15%. 식비 절감 목표 설정할까요?" },
  ],
  ja: [
    { role: "user", text: "毎朝9時にチームのスタンドアップを設定して" },
    { role: "agent", text: "完了。月〜金の午前9時にスタンドアップを作成しました。5分前にリマインダーを送ります。" },
    { role: "user", text: "AIエージェント分野の競合調査をお願い" },
    { role: "agent", text: "ディープリサーチ開始。3分野47社を分析中。推定12分。完了後Telegramにレポート送信します。" },
    { role: "user", text: "毎朝の日本語レッスンをお願いします" },
    { role: "agent", text: "毎朝8時にレッスンを送ります。今日のテーマ：て形 (te-form) の活用法です。" },
  ],
  zh: [
    { role: "user", text: "每天早上9点安排团队站会" },
    { role: "agent", text: "完成。已创建周一至周五上午9点的定期站会。会前5分钟发送提醒。" },
    { role: "user", text: "调研AI Agent领域的竞争对手" },
    { role: "agent", text: "深度调研已启动。正在分析3个垂直领域的47家公司。预计12分钟。完成后通过Telegram发送报告。" },
    { role: "user", text: "帮我分析这个月的支出" },
    { role: "agent", text: "消费分析完成。餐饮占32%(环比+8%)，交通占15%。需要设定餐饮节省目标吗？" },
  ],
  es: [
    { role: "user", text: "Programa una reunión diaria a las 9am" },
    { role: "agent", text: "Listo. Reunión recurrente creada para las 9:00 AM, lun-vie. Te enviaré un recordatorio 5 minutos antes." },
    { role: "user", text: "Investiga competidores en el espacio de agentes IA" },
    { role: "agent", text: "Investigación iniciada. Analizando 47 empresas en 3 verticales. ETA: 12 min. Enviaré el informe por Telegram." },
    { role: "user", text: "Analiza mis gastos de este mes" },
    { role: "agent", text: "Análisis completado. Alimentación 32% (+8% MoM), transporte 15%. ¿Quieres establecer un objetivo de ahorro?" },
  ],
};

const CHAR_DELAY = 28;
const MSG_PAUSE = 700;
const LINE_HEIGHT = 18;
const PADDING = 20;
const HEADER_H = 36;
const CANVAS_HEIGHT = 340;

export function TerminalTyping({ locale = "en" }: TerminalTypingProps) {
  const messages = MESSAGES_BY_LOCALE[locale] ?? MESSAGES_BY_LOCALE.en;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const size = useCanvas(canvasRef, CANVAS_HEIGHT);
  const rafRef = useRef<number>(0);
  const startRef = useRef(0);
  const isVisibleRef = useRef(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0) return;

    function draw(time: number): void {
      const cvs = canvasRef.current;
      if (!cvs || size.width === 0) return;
      const ctx = cvs.getContext("2d");
      if (!ctx) return;

      if (!startRef.current) startRef.current = time;
      const elapsed = time - startRef.current;

      const w = size.width;
      const h = CANVAS_HEIGHT;
      const textAreaW = w - PADDING * 2 - 12;

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(15, 15, 35, 0.95)";
      ctx.beginPath();
      ctx.roundRect(0, 0, w, h, 12);
      ctx.fill();

      ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(0.5, 0.5, w - 1, h - 1, 12);
      ctx.stroke();

      ctx.fillStyle = "rgba(255, 255, 255, 0.03)";
      ctx.fillRect(0, 0, w, HEADER_H);
      ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
      ctx.fillRect(0, HEADER_H - 1, w, 1);

      const dots = ["#FF5F57", "#FFBD2E", "#28C840"];
      for (let i = 0; i < dots.length; i++) {
        ctx.beginPath();
        ctx.arc(PADDING + i * 18, HEADER_H / 2, 5, 0, Math.PI * 2);
        ctx.fillStyle = dots[i];
        ctx.fill();
      }

      ctx.font = `500 12px ${THEME.font}`;
      ctx.fillStyle = THEME.muted;
      ctx.textAlign = "center";
      ctx.fillText("openmagi agent terminal", w / 2, HEADER_H / 2 + 4);
      ctx.textAlign = "left";

      let timeAccum = 0;
      const visibleMsgs: { msg: Message; chars: number; full: boolean }[] = [];

      for (const msg of messages) {
        const charsShown = Math.min(
          msg.text.length,
          Math.max(0, Math.floor((elapsed - timeAccum) / CHAR_DELAY))
        );
        if (charsShown <= 0) break;
        visibleMsgs.push({ msg, chars: charsShown, full: charsShown >= msg.text.length });
        timeAccum += msg.text.length * CHAR_DELAY + MSG_PAUSE;
      }

      let y = HEADER_H + PADDING;
      const bodyFont = `400 13px ${THEME.font}`;
      const labelFont = `600 11px ${THEME.font}`;

      for (const { msg, chars, full } of visibleMsgs) {
        if (y > h - 10) break;

        ctx.font = labelFont;
        ctx.fillStyle = msg.role === "user" ? THEME.primaryLight : THEME.cta;
        ctx.fillText(msg.role === "user" ? "> you" : "> agent", PADDING, y);
        y += 16;

        const prepared = prepareWithSegments(msg.text.slice(0, chars), bodyFont);
        const laid = layoutWithLines(prepared, textAreaW, LINE_HEIGHT);

        ctx.font = bodyFont;
        ctx.fillStyle = msg.role === "user" ? THEME.fg : THEME.secondary;

        for (const line of laid.lines) {
          if (y > h - 10) break;
          ctx.fillText(line.text, PADDING + 12, y);
          y += LINE_HEIGHT;
        }

        if (!full && laid.lines.length > 0) {
          const lastLine = laid.lines[laid.lines.length - 1];
          if (Math.sin(elapsed / 200) > 0) {
            ctx.fillStyle = THEME.primaryLight;
            ctx.fillRect(PADDING + 12 + lastLine.width + 2, y - LINE_HEIGHT - 11, 2, 14);
          }
        }

        y += 8;
      }

      const totalTime = messages.reduce((a, m) => a + m.text.length * CHAR_DELAY + MSG_PAUSE, 0) + 2000;
      if (elapsed > totalTime) startRef.current = time;

      if (isVisibleRef.current) {
        rafRef.current = requestAnimationFrame(draw);
      }
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        isVisibleRef.current = entry.isIntersecting;
        if (entry.isIntersecting) {
          rafRef.current = requestAnimationFrame(draw);
        } else {
          cancelAnimationFrame(rafRef.current);
        }
      },
      { threshold: 0.1 }
    );

    observer.observe(canvas);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [size, messages]);

  return (
    <div className="max-w-3xl mx-auto">
      <canvas ref={canvasRef} className="w-full" />
    </div>
  );
}
