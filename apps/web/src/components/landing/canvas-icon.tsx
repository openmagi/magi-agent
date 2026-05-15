"use client";

import { useRef, useEffect } from "react";
import { THEME } from "./use-canvas";

type IconVariant = "routing" | "agents" | "state" | "lightning" | "shield" | "server" | "lock" | "globe";

interface CanvasIconProps {
  variant: IconVariant;
  size?: number;
}

export function CanvasIcon({ variant, size = 48 }: CanvasIconProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);
  const startRef = useRef(0);
  const isVisibleRef = useRef(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const cx = size / 2;
    const cy = size / 2;

    function draw(time: number): void {
      if (!ctx) return;
      if (!startRef.current) startRef.current = time;
      const t = (time - startRef.current) / 1000;

      ctx.clearRect(0, 0, size, size);

      switch (variant) {
        case "routing":
          drawRouting(ctx, cx, cy, size, t);
          break;
        case "agents":
          drawAgents(ctx, cx, cy, size, t);
          break;
        case "state":
          drawState(ctx, cx, cy, size, t);
          break;
        case "lightning":
          drawLightning(ctx, cx, cy, size, t);
          break;
        case "shield":
          drawShield(ctx, cx, cy, size, t);
          break;
        case "server":
          drawServer(ctx, cx, cy, size, t);
          break;
        case "lock":
          drawLock(ctx, cx, cy, size, t);
          break;
        case "globe":
          drawGlobe(ctx, cx, cy, size, t);
          break;
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
  }, [variant, size]);

  return <canvas ref={canvasRef} className="shrink-0" />;
}

// --- Drawing functions ---

function drawRouting(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const r = s * 0.32;

  // Animated arrows crossing
  for (let i = 0; i < 3; i++) {
    const angle = (t * 0.8 + i * (Math.PI * 2 / 3));
    const x1 = cx + Math.cos(angle) * r;
    const y1 = cy + Math.sin(angle) * r;
    const x2 = cx + Math.cos(angle + Math.PI) * r;
    const y2 = cy + Math.sin(angle + Math.PI) * r;

    const alpha = 0.4 + Math.sin(t * 2 + i) * 0.3;
    ctx.strokeStyle = i === 0 ? THEME.primaryLight : i === 1 ? THEME.cta : THEME.ctaLight;
    ctx.globalAlpha = alpha;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();

    // Arrow head
    const headSize = 4;
    const ha = Math.atan2(y2 - y1, x2 - x1);
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - headSize * Math.cos(ha - 0.5), y2 - headSize * Math.sin(ha - 0.5));
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - headSize * Math.cos(ha + 0.5), y2 - headSize * Math.sin(ha + 0.5));
    ctx.stroke();
  }

  // Center dot
  ctx.globalAlpha = 0.6 + Math.sin(t * 3) * 0.3;
  ctx.fillStyle = THEME.primaryLight;
  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
}

function drawAgents(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const r = s * 0.28;
  const nodeCount = 5;

  for (let i = 0; i < nodeCount; i++) {
    const angle = (i / nodeCount) * Math.PI * 2 + t * 0.3;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;
    const pulse = 0.5 + Math.sin(t * 2.5 + i * 1.2) * 0.4;

    // Connection to center
    ctx.strokeStyle = THEME.primaryLight;
    ctx.globalAlpha = pulse * 0.4;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(x, y);
    ctx.stroke();

    // Node
    ctx.globalAlpha = pulse;
    ctx.fillStyle = i % 2 === 0 ? THEME.primaryLight : THEME.cta;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  }

  // Center hub
  ctx.globalAlpha = 0.8;
  ctx.fillStyle = THEME.fg;
  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
}

function drawState(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const rx = s * 0.28;
  const ry = s * 0.12;
  const layers = 3;
  const layerGap = s * 0.1;
  const rotation = t * 0.5;

  for (let i = 0; i < layers; i++) {
    const yOff = cy - (layers - 1) * layerGap / 2 + i * layerGap;
    const pulse = 0.4 + Math.sin(t * 2 + i * 0.8) * 0.3;

    ctx.globalAlpha = pulse;
    ctx.strokeStyle = i === 0 ? THEME.primaryLight : i === 1 ? THEME.cta : THEME.ctaLight;
    ctx.lineWidth = 1.5;

    // Ellipse
    ctx.beginPath();
    ctx.ellipse(cx, yOff, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();

    // Rotating dot on ellipse
    const dotAngle = rotation + i * (Math.PI * 2 / 3);
    const dotX = cx + Math.cos(dotAngle) * rx;
    const dotY = yOff + Math.sin(dotAngle) * ry;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.arc(dotX, dotY, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.globalAlpha = 1;
}

function drawLightning(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const flash = Math.sin(t * 4) > 0.3 ? 1 : 0.4;

  // Lightning bolt path
  const scale = s * 0.025;
  ctx.save();
  ctx.translate(cx - 8 * scale, cy - 11 * scale);
  ctx.scale(scale, scale);

  ctx.globalAlpha = flash;
  const grad = ctx.createLinearGradient(0, 0, 16, 22);
  grad.addColorStop(0, THEME.primaryLight);
  grad.addColorStop(1, THEME.cta);
  ctx.fillStyle = grad;

  ctx.beginPath();
  ctx.moveTo(13, 0);
  ctx.lineTo(4, 11);
  ctx.lineTo(9, 11);
  ctx.lineTo(3, 22);
  ctx.lineTo(12, 11);
  ctx.lineTo(7, 11);
  ctx.closePath();
  ctx.fill();

  ctx.restore();

  // Sparks
  for (let i = 0; i < 4; i++) {
    const sparkAngle = t * 3 + i * (Math.PI / 2);
    const sparkR = s * 0.3 + Math.sin(t * 5 + i * 2) * s * 0.08;
    const sx = cx + Math.cos(sparkAngle) * sparkR;
    const sy = cy + Math.sin(sparkAngle) * sparkR;
    const sparkAlpha = 0.3 + Math.sin(t * 6 + i) * 0.3;

    ctx.globalAlpha = sparkAlpha;
    ctx.fillStyle = i % 2 === 0 ? THEME.primaryLight : THEME.ctaLight;
    ctx.beginPath();
    ctx.arc(sx, sy, 1.5, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.globalAlpha = 1;
}

function drawShield(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const pulse = 0.6 + Math.sin(t * 2) * 0.3;
  const r = s * 0.3;

  ctx.globalAlpha = pulse;
  ctx.strokeStyle = THEME.primaryLight;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.quadraticCurveTo(cx + r, cy - r * 0.6, cx + r, cy);
  ctx.quadraticCurveTo(cx + r * 0.6, cy + r, cx, cy + r * 1.1);
  ctx.quadraticCurveTo(cx - r * 0.6, cy + r, cx - r, cy);
  ctx.quadraticCurveTo(cx - r, cy - r * 0.6, cx, cy - r);
  ctx.stroke();

  ctx.globalAlpha = pulse * 0.15;
  ctx.fillStyle = THEME.primary;
  ctx.fill();

  ctx.globalAlpha = pulse;
  ctx.strokeStyle = THEME.cta;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx - r * 0.3, cy);
  ctx.lineTo(cx - r * 0.05, cy + r * 0.25);
  ctx.lineTo(cx + r * 0.35, cy - r * 0.2);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

function drawServer(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const bw = s * 0.5;
  const bh = s * 0.14;
  const gap = 3;
  const startY = cy - (bh * 3 + gap * 2) / 2;

  for (let i = 0; i < 3; i++) {
    const y = startY + i * (bh + gap);
    const pulse = 0.4 + Math.sin(t * 2 + i * 0.8) * 0.3;

    ctx.globalAlpha = pulse;
    ctx.strokeStyle = THEME.primaryLight;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(cx - bw / 2, y, bw, bh, 3);
    ctx.stroke();

    const ledOn = Math.sin(t * 3 + i * 1.5) > 0;
    ctx.fillStyle = ledOn ? "#28C840" : THEME.muted;
    ctx.globalAlpha = ledOn ? 0.9 : 0.3;
    ctx.beginPath();
    ctx.arc(cx + bw / 2 - 6, y + bh / 2, 2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawLock(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const pulse = 0.5 + Math.sin(t * 2) * 0.3;
  const r = s * 0.16;
  const bodyW = s * 0.36;
  const bodyH = s * 0.28;

  ctx.globalAlpha = pulse;
  ctx.strokeStyle = THEME.primaryLight;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy - bodyH * 0.3, r, Math.PI, 0);
  ctx.stroke();

  ctx.fillStyle = THEME.primary;
  ctx.globalAlpha = pulse * 0.3;
  ctx.beginPath();
  ctx.roundRect(cx - bodyW / 2, cy - bodyH * 0.15, bodyW, bodyH, 3);
  ctx.fill();
  ctx.strokeStyle = THEME.primaryLight;
  ctx.globalAlpha = pulse;
  ctx.stroke();

  ctx.fillStyle = THEME.cta;
  ctx.globalAlpha = 0.5 + Math.sin(t * 3) * 0.4;
  ctx.beginPath();
  ctx.arc(cx, cy + bodyH * 0.15, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillRect(cx - 1, cy + bodyH * 0.15, 2, bodyH * 0.25);
  ctx.globalAlpha = 1;
}

function drawGlobe(ctx: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number): void {
  const r = s * 0.3;
  const rotation = t * 0.4;

  ctx.globalAlpha = 0.5;
  ctx.strokeStyle = THEME.primaryLight;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.stroke();

  for (let i = 0; i < 3; i++) {
    const offset = rotation + i * (Math.PI / 3);
    const eccentricity = Math.abs(Math.cos(offset)) * r;
    ctx.globalAlpha = 0.3 + Math.sin(t * 2 + i) * 0.2;
    ctx.beginPath();
    ctx.ellipse(cx, cy, eccentricity, r, 0, 0, Math.PI * 2);
    ctx.stroke();
  }

  for (let i = -1; i <= 1; i++) {
    const y = cy + i * r * 0.45;
    const pr = Math.sqrt(r * r - (i * r * 0.45) ** 2);
    ctx.globalAlpha = 0.25;
    ctx.beginPath();
    ctx.ellipse(cx, y, pr, pr * 0.2, 0, 0, Math.PI * 2);
    ctx.stroke();
  }

  const dotAngle = t * 1.5;
  ctx.globalAlpha = 0.8;
  ctx.fillStyle = THEME.cta;
  ctx.beginPath();
  ctx.arc(cx + Math.cos(dotAngle) * r * 0.8, cy + Math.sin(dotAngle) * r * 0.5, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
}
