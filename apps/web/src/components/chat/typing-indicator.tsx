"use client";

export function TypingIndicator() {
  return (
    <div className="flex justify-start mb-3">
      <div className="bg-black/[0.04] border border-black/10 rounded-2xl px-4 py-3">
        <div className="flex items-center gap-1">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-2 h-2 rounded-full bg-secondary animate-bounce"
              style={{ animationDelay: `${i * 0.15}s`, animationDuration: "0.6s" }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
