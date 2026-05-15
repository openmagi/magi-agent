"use client";

import { useRef, useEffect, useState } from "react";
import { prepareWithSegments, layoutWithLines } from "@chenglou/pretext";
import { useCanvas, THEME } from "./use-canvas";

interface HeroScrambleProps {
  line1: string;
  line2: string;
}

const CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%&*";
const SCRAMBLE_DURATION = 1200;
const STAGGER_PER_CHAR = 30;

function randomChar(): string {
  return CHARS[Math.floor(Math.random() * CHARS.length)];
}

export function HeroScramble({ line1, line2 }: HeroScrambleProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [canvasHeight, setCanvasHeight] = useState(200);
  const size = useCanvas(canvasRef, canvasHeight);
  const startTimeRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const hasStartedRef = useRef(false);

  useEffect(() => {
    if (size.width === 0) return;

    function draw(): void {
      const canvas = canvasRef.current;
      if (!canvas || size.width === 0) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const now = performance.now();
      const elapsed = now - startTimeRef.current;

      const isMobile = size.width < 640;
      const isTablet = size.width < 1024;
      const fontSize = isMobile ? 36 : isTablet ? 48 : 64;
      const lineHeight = fontSize * 1.12;
      const fontStr = `700 ${fontSize}px ${THEME.font}`;

      const prepared1 = prepareWithSegments(line1, fontStr);
      const prepared2 = prepareWithSegments(line2, fontStr);
      const layout1 = layoutWithLines(prepared1, size.width, lineHeight);
      const layout2 = layoutWithLines(prepared2, size.width, lineHeight);

      const gap = 4;
      const totalHeight = layout1.height + gap + layout2.height;
      if (Math.abs(canvasHeight - totalHeight) > 4) {
        setCanvasHeight(totalHeight);
        return;
      }

      ctx.clearRect(0, 0, size.width, canvasHeight);

      type CharInfo = { char: string; x: number; y: number; index: number; isLine1: boolean };
      const allChars: CharInfo[] = [];
      let charIndex = 0;

      let y = lineHeight * 0.85;
      ctx.font = fontStr;
      for (const line of layout1.lines) {
        let x = (size.width - line.width) / 2;
        for (const ch of line.text) {
          const w = ctx.measureText(ch).width;
          if (ch.trim()) {
            allChars.push({ char: ch, x, y, index: charIndex, isLine1: true });
            charIndex++;
          }
          x += w;
        }
        y += lineHeight;
      }

      y = layout1.height + gap + lineHeight * 0.85;
      for (const line of layout2.lines) {
        let x = (size.width - line.width) / 2;
        for (const ch of line.text) {
          const w = ctx.measureText(ch).width;
          if (ch.trim()) {
            allChars.push({ char: ch, x, y, index: charIndex, isLine1: false });
            charIndex++;
          }
          x += w;
        }
        y += lineHeight;
      }

      const gradient = ctx.createLinearGradient(0, 0, size.width, layout1.height);
      gradient.addColorStop(0, THEME.primaryLight);
      gradient.addColorStop(1, THEME.cta);

      for (const c of allChars) {
        const charElapsed = elapsed - c.index * STAGGER_PER_CHAR;
        ctx.font = fontStr;

        if (charElapsed >= SCRAMBLE_DURATION) {
          ctx.fillStyle = c.isLine1 ? gradient : THEME.fg;
          ctx.globalAlpha = 1;
          ctx.fillText(c.char, c.x, c.y);
        } else if (charElapsed > 0) {
          const progress = charElapsed / SCRAMBLE_DURATION;
          ctx.globalAlpha = 0.3 + progress * 0.7;
          ctx.fillStyle = c.isLine1 ? "rgba(167, 139, 250, 1)" : "rgba(226, 232, 240, 1)";
          ctx.fillText(progress > 0.8 ? c.char : randomChar(), c.x, c.y);
          ctx.globalAlpha = 1;
        }
      }

      const allDone = elapsed > allChars.length * STAGGER_PER_CHAR + SCRAMBLE_DURATION;
      if (!allDone) {
        rafRef.current = requestAnimationFrame(draw);
      }
    }

    // Start on intersection
    if (hasStartedRef.current) {
      // Already started — just re-draw (resize case)
      startTimeRef.current = performance.now();
      rafRef.current = requestAnimationFrame(draw);
      return () => cancelAnimationFrame(rafRef.current);
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasStartedRef.current) {
          hasStartedRef.current = true;
          startTimeRef.current = performance.now();
          rafRef.current = requestAnimationFrame(draw);
          observer.disconnect();
        }
      },
      { threshold: 0.3 }
    );

    const canvas = canvasRef.current;
    if (canvas) observer.observe(canvas);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [size, canvasHeight, line1, line2]);

  return (
    <div className="relative w-full">
      <canvas ref={canvasRef} className="w-full" />
      <h1 className="sr-only">
        <span>{line1}</span> <span>{line2}</span>
      </h1>
    </div>
  );
}
