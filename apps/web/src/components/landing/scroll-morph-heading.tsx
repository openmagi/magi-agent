"use client";

import { useRef, useEffect, useState } from "react";
import { prepareWithSegments, layoutWithLines } from "@chenglou/pretext";
import { useCanvas, THEME } from "./use-canvas";

interface ScrollMorphHeadingProps {
  text: string;
  gradient?: boolean;
  align?: "center" | "left";
}

const ANIM_DURATION = 600;

export function ScrollMorphHeading({ text, gradient = true, align = "center" }: ScrollMorphHeadingProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [canvasHeight, setCanvasHeight] = useState(60);
  const size = useCanvas(canvasRef, canvasHeight);
  const animStartRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const doneRef = useRef(false);

  // Reset animation when text changes (e.g. language switch)
  useEffect(() => {
    doneRef.current = false;
  }, [text]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || size.width === 0) return;

    // Measure required height first
    const isMobile = size.width < 640;
    const fontSize = isMobile ? 28 : 44;
    const lineHeight = fontSize * 1.2;
    const fontStr = `700 ${fontSize}px ${THEME.font}`;
    const prepared = prepareWithSegments(text, fontStr);
    const laid = layoutWithLines(prepared, size.width, lineHeight);
    const requiredHeight = laid.height + 8;

    if (Math.abs(canvasHeight - requiredHeight) > 4) {
      setCanvasHeight(requiredHeight);
      return;
    }

    function draw(): void {
      const canvas = canvasRef.current;
      if (!canvas || size.width === 0) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const now = performance.now();
      const elapsed = now - animStartRef.current;
      const progress = Math.min(elapsed / ANIM_DURATION, 1);
      const ease = 1 - Math.pow(1 - progress, 3);

      ctx.clearRect(0, 0, size.width, canvasHeight);
      ctx.font = fontStr;

      const grad = ctx.createLinearGradient(0, 0, size.width, 0);
      grad.addColorStop(0, THEME.primaryLight);
      grad.addColorStop(1, THEME.cta);

      for (let lineIdx = 0; lineIdx < laid.lines.length; lineIdx++) {
        const line = laid.lines[lineIdx];
        const baseX = align === "center" ? (size.width - line.width) / 2 : 0;
        const baseY = lineHeight * 0.85 + lineIdx * lineHeight;

        let x = baseX;
        let charIdx = 0;

        for (const ch of line.text) {
          const charW = ctx.measureText(ch).width;
          if (ch.trim()) {
            const totalChars = line.text.replace(/\s/g, "").length;
            const charProgress = Math.max(0, Math.min(1, ease * 1.5 - (charIdx / totalChars) * 0.5));

            ctx.fillStyle = gradient ? grad : THEME.fg;
            ctx.globalAlpha = charProgress;
            ctx.fillText(ch, x, baseY + (1 - charProgress) * 20);
            ctx.globalAlpha = 1;
            charIdx++;
          }
          x += charW;
        }
      }

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(draw);
      } else {
        doneRef.current = true;
      }
    }

    // If already visible, start drawing immediately (handles language switch)
    const rect = container.getBoundingClientRect();
    const isVisible = rect.top < window.innerHeight && rect.bottom > 0;
    if (isVisible && !doneRef.current) {
      animStartRef.current = performance.now();
      rafRef.current = requestAnimationFrame(draw);
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !doneRef.current) {
          animStartRef.current = performance.now();
          rafRef.current = requestAnimationFrame(draw);
        }
      },
      { threshold: 0.5 }
    );

    observer.observe(container);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [size, text, gradient, canvasHeight]);

  return (
    <div ref={containerRef} className="relative w-full">
      <canvas ref={canvasRef} className="w-full" />
      <h2 className="sr-only">{text}</h2>
    </div>
  );
}
