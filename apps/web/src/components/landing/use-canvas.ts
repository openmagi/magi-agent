"use client";

import { useEffect, useState } from "react";

interface CanvasSize {
  width: number;
  height: number;
  dpr: number;
}

export function useCanvas(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  heightOverride?: number
): CanvasSize {
  const [size, setSize] = useState<CanvasSize>({ width: 0, height: 0, dpr: 1 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const parent = canvas.parentElement;
    if (!parent) return;

    const update = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = parent.getBoundingClientRect();
      const w = rect.width;
      const h = heightOverride ?? rect.height;

      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      const ctx = canvas.getContext("2d");
      if (ctx) ctx.scale(dpr, dpr);

      setSize({ width: w, height: h, dpr });
    };

    const ro = new ResizeObserver(update);
    ro.observe(parent);
    update();

    return () => ro.disconnect();
  }, [canvasRef, heightOverride]);

  return size;
}

/** Shared theme constants matching globals.css */
export const THEME = {
  bg: "#FAFAFA",
  fg: "#1A1A2E",
  secondary: "#64748B",
  muted: "#94A3B8",
  primary: "#7C3AED",
  primaryLight: "#6D28D9",
  cta: "#E11D48",
  ctaLight: "#F43F5E",
  font: '"Plus Jakarta Sans", system-ui, sans-serif',
} as const;
