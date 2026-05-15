"use client";

import { useRef, useEffect } from "react";
import { prepareWithSegments, layoutNextLine } from "@chenglou/pretext";
import type { LayoutCursor } from "@chenglou/pretext";
import { useCanvas, THEME } from "./use-canvas";

interface OrbTextFlowProps {
  title: string;
  description: string;
}

const ORB_RADIUS = 50;
const LINE_HEIGHT = 20;
const PADDING = 24;
const CANVAS_HEIGHT = 260;

export function OrbTextFlow({ title, description }: OrbTextFlowProps) {
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
      const t = (time - startRef.current) / 1000;

      const w = size.width;
      const h = CANVAS_HEIGHT;
      ctx.clearRect(0, 0, w, h);

      const orbX = w * 0.72 + Math.sin(t * 0.6) * 25;
      const orbY = h * 0.5 + Math.sin(t * 0.8) * 18 + Math.cos(t * 0.4) * 8;

      // Glow
      const glowGrad = ctx.createRadialGradient(orbX, orbY, 0, orbX, orbY, ORB_RADIUS * 2.5);
      glowGrad.addColorStop(0, "rgba(124, 58, 237, 0.25)");
      glowGrad.addColorStop(0.4, "rgba(244, 63, 94, 0.08)");
      glowGrad.addColorStop(1, "transparent");
      ctx.fillStyle = glowGrad;
      ctx.fillRect(0, 0, w, h);

      // Orb
      const orbGrad = ctx.createRadialGradient(orbX - 12, orbY - 12, 0, orbX, orbY, ORB_RADIUS);
      orbGrad.addColorStop(0, THEME.primaryLight);
      orbGrad.addColorStop(0.6, THEME.primary);
      orbGrad.addColorStop(1, THEME.cta);
      ctx.beginPath();
      ctx.arc(orbX, orbY, ORB_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = orbGrad;
      ctx.fill();

      // Shine
      ctx.beginPath();
      ctx.arc(orbX - 10, orbY - 10, ORB_RADIUS * 0.25, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 255, 255, 0.12)";
      ctx.fill();

      // Title
      ctx.font = `700 18px ${THEME.font}`;
      ctx.fillStyle = THEME.fg;
      ctx.fillText(title, PADDING, PADDING + 18);

      // Text flow around orb
      const textFont = `400 13px ${THEME.font}`;
      const prepared = prepareWithSegments(description, textFont);
      let cursor: LayoutCursor = { segmentIndex: 0, graphemeIndex: 0 };
      let y = PADDING + 40;

      ctx.font = textFont;
      ctx.fillStyle = THEME.secondary;

      while (y < h - PADDING) {
        const lineTop = y - LINE_HEIGHT;
        const lineBottom = y;
        let availableWidth = w - PADDING * 2;

        if (lineBottom > orbY - ORB_RADIUS - 14 && lineTop < orbY + ORB_RADIUS + 14) {
          availableWidth = Math.max(80, orbX - ORB_RADIUS - 16 - PADDING);
        }

        const line = layoutNextLine(prepared, cursor, availableWidth);
        if (line === null) break;
        ctx.fillText(line.text, PADDING, y);
        cursor = line.end;
        y += LINE_HEIGHT;
      }

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
  }, [size, title, description]);

  return (
    <canvas
      ref={canvasRef}
      className="w-full rounded-2xl"
      style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.06)" }}
    />
  );
}
