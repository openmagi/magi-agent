"use client";

import { useState, useEffect, useCallback } from "react";
import { SidebarNav } from "@/components/dashboard/sidebar-nav";

export function MobileSidebar() {
  const [open, setOpen] = useState(false);

  // Body scroll lock
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = "";
      };
    }
  }, [open]);

  // ESC key handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
  }, []);

  useEffect(() => {
    if (open) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [open, handleKeyDown]);

  return (
    <>
      {/* Mobile header bar */}
      <div className="md:hidden sticky top-0 z-30 flex items-center gap-3 px-4 py-3 border-b border-gray-200 bg-background/95 backdrop-blur-xl">
        <button
          onClick={() => setOpen(true)}
          className="p-2 -ml-2 text-gray-700 hover:text-gray-900 rounded-lg hover:bg-gray-100 transition-colors"
          aria-label="Open menu"
          aria-expanded={open}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
      </div>

      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Drawer panel */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-72 transform transition-transform duration-300 ease-in-out md:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <SidebarNav
          onNavigate={() => setOpen(false)}
          className="!w-full !min-h-full"
        />
      </div>
    </>
  );
}
