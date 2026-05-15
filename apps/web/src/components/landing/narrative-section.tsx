"use client";

import { useRef, useEffect } from "react";

interface NarrativeSectionProps {
  id: string;
  onVisible: (id: string) => void;
  children: React.ReactNode;
  className?: string;
}

export function NarrativeSection({ id, onVisible, children, className }: NarrativeSectionProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) onVisible(id);
      },
      { threshold: 0.4, rootMargin: "-10% 0px -40% 0px" }
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [id, onVisible]);

  return (
    <div ref={ref} id={id} className={`min-h-[60vh] scroll-mt-24 sm:scroll-mt-28 flex flex-col justify-center py-16 sm:py-24 ${className ?? ""}`}>
      {children}
    </div>
  );
}
