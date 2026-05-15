"use client";

import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Channel, ChannelMemoryMode } from "@/lib/chat/types";
import { useChatStore } from "@/lib/chat/chat-store";
import { useI18n } from "@/lib/i18n";
import { localizeCategory, localizeChannel, DEFAULT_CHANNELS } from "@/lib/chat/channel-i18n";
import {
  formatChannelBaseLabel,
  formatChannelMemoryBadgeLabel,
  formatChannelMemoryLabel,
} from "@/lib/chat/channel-memory-mode";

const DEFAULT_CATEGORIES = ["General"];

type FlatItem =
  | { type: "header"; key: string; title: string }
  | { type: "channel"; key: string; channel: Channel };

function rebuildChannelsFromFlat(items: FlatItem[]): Channel[] {
  const channels: Channel[] = [];
  let currentCategory = "Other";
  let position = 0;
  for (const item of items) {
    if (item.type === "header") {
      currentCategory = item.title;
    } else {
      channels.push({ ...item.channel, category: currentCategory, position });
      position++;
    }
  }
  return channels;
}

/* ---------- Inline icon buttons ---------- */

function PencilIcon({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onPointerDown={(e) => e.stopPropagation()}
      className="p-0.5 text-secondary/40 hover:text-primary transition-colors cursor-pointer shrink-0"
      title="Rename"
    >
      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
      </svg>
    </button>
  );
}

function TrashIcon({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onPointerDown={(e) => e.stopPropagation()}
      className="p-0.5 text-secondary/40 hover:text-red-400 transition-colors cursor-pointer shrink-0"
      title="Delete"
    >
      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
      </svg>
    </button>
  );
}

function MemoryOffIcon() {
  return (
    <span title="Memory off" className="shrink-0 text-secondary/45" aria-label="Memory off">
      <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 10.5a2.25 2.25 0 003 3" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.88 5.09A10.55 10.55 0 0112 4.88c5.25 0 8.25 4.62 9 6.12-.32.64-1.12 1.9-2.37 3.08" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.61 6.62C4.73 7.89 3.54 9.83 3 11c.75 1.5 3.75 6.12 9 6.12 1.17 0 2.25-.23 3.22-.62" />
      </svg>
    </span>
  );
}

function MemoryReadOnlyIcon() {
  return (
    <span title="Memory read-only" className="shrink-0 text-secondary/45" aria-label="Memory read-only">
      <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 5.5A2.5 2.5 0 016.5 3H20v15H7a3 3 0 00-3 3V5.5z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 7h8M8 10h6" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M16 17l4 4M20 17l-4 4" />
      </svg>
    </span>
  );
}

function MemoryModeIcon({ mode }: { mode?: ChannelMemoryMode }) {
  if (mode === "incognito") return <MemoryOffIcon />;
  if (mode === "read_only") return <MemoryReadOnlyIcon />;
  return null;
}

function MemoryModeBadge({
  mode,
  active = false,
}: {
  mode?: ChannelMemoryMode;
  active?: boolean;
}) {
  const label = formatChannelMemoryLabel(mode);
  const badgeLabel = formatChannelMemoryBadgeLabel(mode);
  if (!label || !badgeLabel) return null;

  return (
    <span
      title={label}
      aria-label={label}
      className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none ${
        active
          ? "border-primary/20 bg-white/70 text-primary-light"
          : "border-black/[0.08] bg-black/[0.035] text-secondary/70 group-hover:text-foreground/70"
      }`}
    >
      {badgeLabel}
    </span>
  );
}

/* ---------- Sortable item components ---------- */

function SortableHeader({ id, title, localizedTitle, isCustom, isRenaming, renameValue, onDeleteCategory, onStartRename, onRenameChange, onRenameSubmit, onRenameCancel }: {
  id: string;
  title: string;
  localizedTitle: string;
  isCustom: boolean;
  isRenaming: boolean;
  renameValue: string;
  onDeleteCategory: (name: string) => void;
  onStartRename: (key: string, currentName: string) => void;
  onRenameChange: (value: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming && inputRef.current) inputRef.current.focus();
  }, [isRenaming]);

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center justify-between px-2 py-1.5 mt-3 first:mt-0 rounded-md ${
        isDragging ? "bg-primary/10 ring-1 ring-primary/30 shadow-md" : "bg-black/[0.03]"
      }`}
    >
      <div className="flex items-center gap-1.5 min-w-0 flex-1" {...attributes} {...listeners}>
        <span className="text-secondary/40 text-xs cursor-grab active:cursor-grabbing shrink-0">☰</span>
        <svg className="w-3 h-3 text-slate-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
        </svg>
        {isRenaming ? (
          <input
            ref={inputRef}
            value={renameValue}
            onChange={(e) => onRenameChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onRenameSubmit();
              if (e.key === "Escape") onRenameCancel();
            }}
            onBlur={onRenameSubmit}
            className="text-[11px] font-semibold text-foreground uppercase tracking-wider bg-white border border-primary/40 rounded px-1.5 py-0.5 outline-none min-w-0 flex-1"
            onPointerDown={(e) => e.stopPropagation()}
          />
        ) : (
          <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider truncate">
            {localizedTitle}
          </h3>
        )}
      </div>
      {isCustom && !isRenaming && (
        <div className="flex items-center gap-0.5 ml-1 shrink-0">
          <PencilIcon onClick={() => onStartRename(id, title)} />
          <TrashIcon onClick={() => {
            if (confirm(`Delete "${localizedTitle}"? Channels will be moved to "Other".`)) {
              onDeleteCategory(title);
            }
          }} />
        </div>
      )}
    </div>
  );
}

function SortableChannel({ id, channel, isActive, isCustom, canDelete, isRenaming, renameValue, onStartRename, onRenameChange, onRenameSubmit, onRenameCancel, onDelete }: {
  id: string;
  channel: Channel;
  isActive: boolean;
  isCustom: boolean;
  canDelete: boolean;
  isRenaming: boolean;
  renameValue: string;
  onStartRename: (key: string, currentName: string) => void;
  onRenameChange: (value: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
  onDelete: (name: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming && inputRef.current) inputRef.current.focus();
  }, [isRenaming]);

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-1.5 pl-6 pr-2 py-1.5 rounded-lg text-sm transition-colors group ${
        isActive ? "bg-primary/5 text-foreground" : "text-secondary"
      } ${isDragging ? "bg-black/5 shadow-lg ring-1 ring-primary/30 z-10" : ""}`}
    >
      <span
        className="text-secondary/50 text-xs cursor-grab active:cursor-grabbing shrink-0"
        {...attributes}
        {...listeners}
      >
        ☰
      </span>
      <span className="text-secondary/40 shrink-0">#</span>
      {isRenaming ? (
        <input
          ref={inputRef}
          value={renameValue}
          onChange={(e) => onRenameChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRenameSubmit();
            if (e.key === "Escape") onRenameCancel();
          }}
          onBlur={onRenameSubmit}
          className="text-sm text-foreground bg-white border border-primary/40 rounded px-1.5 py-0.5 outline-none min-w-0 flex-1"
          onPointerDown={(e) => e.stopPropagation()}
        />
      ) : (
        <>
          <span className="truncate flex-1">
            {formatChannelBaseLabel(channel)}
          </span>
          <MemoryModeBadge mode={channel.memory_mode} active={isActive} />
          <div className="flex items-center gap-0.5 shrink-0">
            {isCustom && (
              <PencilIcon onClick={() => onStartRename(id, channel.display_name || channel.name)} />
            )}
            {canDelete && (
              <TrashIcon onClick={() => onDelete(channel.name)} />
            )}
          </div>
        </>
      )}
    </div>
  );
}


/* ---------- Main component ---------- */

interface ChatSidebarProps {
  channels: Channel[];
  activeChannel: string;
  currentBotId?: string;
  botName: string;
  botStatus: string;
  bots?: BotInfo[];
  maxBots?: number;
  editing: boolean;
  customCategories: string[];
  refreshing: boolean;
  mobileOpen: boolean;
  onChannelSelect: (name: string) => void;
  onDeleteChannel: (name: string) => void;
  onCreateChannel: (name: string, memoryMode?: ChannelMemoryMode) => void;
  onCreateCategory: (name: string) => void;
  onDeleteCategory: (name: string) => void;
  onRefreshChannels: () => void;
  onToggleEdit: () => void;
  onCancelEdit: () => void;
  onMobileClose: () => void;
  onReorderChannels: (channels: Channel[]) => void;
  onRenameChannel: (channelName: string, newDisplayName: string) => void;
  onRenameCategory: (oldName: string, newName: string) => void;
}

interface BotInfo {
  id: string;
  name: string;
  status: string;
}

export function ChatSidebar({
  channels,
  activeChannel,
  currentBotId: currentBotIdProp,
  botName,
  botStatus,
  bots = [],
  maxBots = 1,
  editing,
  customCategories,
  refreshing,
  mobileOpen,
  onChannelSelect,
  onDeleteChannel,
  onCreateChannel,
  onCreateCategory,
  onDeleteCategory,
  onRefreshChannels,
  onToggleEdit,
  onCancelEdit,
  onMobileClose,
  onReorderChannels,
  onRenameChannel,
  onRenameCategory,
}: ChatSidebarProps) {
  const router = useRouter();
  const [showNewChannel, setShowNewChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState("");
  const [newChannelMemoryMode, setNewChannelMemoryMode] = useState<ChannelMemoryMode>("normal");
  const [showNewCategory, setShowNewCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");
  const [showAddMenu, setShowAddMenu] = useState(false);
  const [botMenuOpen, setBotMenuOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; channelName: string } | null>(null);
  const [renamingKey, setRenamingKey] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const { locale } = useI18n();
  const routeParams = useParams();
  const currentBotId = currentBotIdProp ?? (routeParams.botId as string | undefined);
  const currentBot = bots.find((bot) => bot.id === currentBotId) ?? {
    id: currentBotId ?? "",
    name: botName,
    status: botStatus,
  };
  const showBotSwitcher = maxBots > 1 || bots.length > 1;
  const canAddBot = bots.length < maxBots;

  const buildBotChatHref = useCallback(
    (targetBotId: string) => {
      const suffix = activeChannel ? `/${encodeURIComponent(activeChannel)}` : "";
      return `/dashboard/${targetBotId}/chat${suffix}`;
    },
    [activeChannel],
  );

  const handleAddBot = useCallback(() => {
    try {
      sessionStorage.setItem("clawy:open-add-bot", "1");
    } catch { /* ignore */ }
    setBotMenuOpen(false);
    onMobileClose();
    router.push(currentBotId ? `/dashboard/${currentBotId}/overview` : "/dashboard/new");
  }, [currentBotId, onMobileClose, router]);

  const handleDone = useCallback(() => {
    setRenamingKey(null);
    setRenameValue("");
    onToggleEdit();
  }, [onToggleEdit]);

  const handleCancel = useCallback(() => {
    setRenamingKey(null);
    setRenameValue("");
    onCancelEdit();
  }, [onCancelEdit]);

  const handleStartRename = useCallback((key: string, currentName: string) => {
    setRenamingKey(key);
    setRenameValue(currentName);
  }, []);

  const handleRenameSubmit = useCallback(() => {
    if (!renamingKey || !renameValue.trim()) {
      setRenamingKey(null);
      setRenameValue("");
      return;
    }
    const trimmed = renameValue.trim();
    if (renamingKey.startsWith("header-")) {
      const oldCategoryName = renamingKey.slice("header-".length);
      if (trimmed !== oldCategoryName) {
        onRenameCategory(oldCategoryName, trimmed);
      }
    } else {
      const ch = channels.find((c) => c.id === renamingKey);
      if (ch && trimmed !== (ch.display_name || ch.name)) {
        onRenameChannel(ch.name, trimmed);
      }
    }
    setRenamingKey(null);
    setRenameValue("");
  }, [renamingKey, renameValue, channels, onRenameChannel, onRenameCategory]);

  const handleRenameCancel = useCallback(() => {
    setRenamingKey(null);
    setRenameValue("");
  }, []);

  const categoryOrder = useMemo(
    () => [...DEFAULT_CATEGORIES, ...customCategories],
    [customCategories],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, Channel[]>();
    for (const ch of channels) {
      const cat = ch.category || "General";
      const list = map.get(cat);
      if (list) list.push(ch);
      else map.set(cat, [ch]);
    }
    const sections: { title: string; channels: Channel[] }[] = [];
    for (const cat of categoryOrder) {
      const data = map.get(cat);
      if (data) {
        sections.push({ title: cat, channels: data.sort((a, b) => a.position - b.position) });
        map.delete(cat);
      } else if (editing) {
        sections.push({ title: cat, channels: [] });
      }
    }
    for (const [cat, data] of map) {
      sections.push({ title: cat, channels: data.sort((a, b) => a.position - b.position) });
    }
    return sections;
  }, [channels, categoryOrder, editing]);

  const flatItems = useMemo((): FlatItem[] => {
    const items: FlatItem[] = [];
    for (const section of grouped) {
      items.push({ type: "header", key: `header-${section.title}`, title: section.title });
      for (const ch of section.channels) {
        items.push({ type: "channel", key: ch.id, channel: ch });
      }
    }
    return items;
  }, [grouped]);

  const flatIds = useMemo(() => flatItems.map((item) => item.key), [flatItems]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
  );

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = flatItems.findIndex((item) => item.key === active.id);
    const newIndex = flatItems.findIndex((item) => item.key === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const draggedItem = flatItems[oldIndex];

    if (draggedItem.type === "header") {
      let groupEnd = oldIndex + 1;
      while (groupEnd < flatItems.length && flatItems[groupEnd].type !== "header") {
        groupEnd++;
      }
      const group = flatItems.slice(oldIndex, groupEnd);
      const remaining = [...flatItems.slice(0, oldIndex), ...flatItems.slice(groupEnd)];
      let targetIdx = remaining.findIndex((item) => item.key === over.id);
      if (targetIdx === -1) targetIdx = remaining.length;
      if (remaining[targetIdx]?.type === "channel") {
        let hi = targetIdx;
        while (hi >= 0 && remaining[hi].type !== "header") hi--;
        targetIdx = hi >= 0 ? hi : 0;
      }
      const reordered = [...remaining.slice(0, targetIdx), ...group, ...remaining.slice(targetIdx)];
      onReorderChannels(rebuildChannelsFromFlat(reordered));
    } else {
      const reordered = arrayMove(flatItems, oldIndex, newIndex);
      onReorderChannels(rebuildChannelsFromFlat(reordered));
    }
  }, [flatItems, onReorderChannels]);

  const handleCreateChannel = useCallback(() => {
    const name = newChannelName.trim();
    if (!name) return;
    onCreateChannel(name, newChannelMemoryMode);
    setNewChannelName("");
    setNewChannelMemoryMode("normal");
    setShowNewChannel(false);
    // Stay in edit mode — don't call onToggleEdit
  }, [newChannelName, newChannelMemoryMode, onCreateChannel]);

  const handleCreateCategory = useCallback(() => {
    const name = newCategoryName.trim();
    if (!name) return;
    if (categoryOrder.includes(name)) return;
    onCreateCategory(name);
    setNewCategoryName("");
    setShowNewCategory(false);
  }, [newCategoryName, categoryOrder, onCreateCategory]);

  const handleContextMenu = useCallback((e: React.MouseEvent, channelName: string) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, channelName });
  }, []);

  const handleChannelClick = useCallback((name: string) => {
    useChatStore.getState().markChannelRead(name);
    onChannelSelect(name);
    onMobileClose();
  }, [onChannelSelect, onMobileClose]);

  /* ---- Shared sidebar inner content ---- */
  const sidebarInner = (
    <>
      {/* Bot info */}
      <div className="p-4 border-b border-black/8">
        <div className="relative">
          <div className="flex items-start gap-2">
            <button
              type="button"
              aria-label="Switch bot"
              aria-expanded={botMenuOpen}
              disabled={!showBotSwitcher}
              onClick={() => showBotSwitcher && setBotMenuOpen((open) => !open)}
              className={`min-w-0 flex-1 text-left rounded-xl transition-colors ${
                showBotSwitcher ? "hover:bg-black/[0.04] cursor-pointer" : "cursor-default"
              }`}
            >
              <div className="flex min-w-0 items-center gap-1.5">
                <h2 className="truncate text-sm font-semibold text-foreground">{currentBot.name}</h2>
                {showBotSwitcher && (
                  <svg
                    className={`h-3.5 w-3.5 shrink-0 text-secondary/50 transition-transform ${botMenuOpen ? "rotate-180" : ""}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 111.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z" clipRule="evenodd" />
                  </svg>
                )}
              </div>
              <div className="mt-1 flex items-center gap-1.5">
                <div className={`h-2 w-2 rounded-full ${currentBot.status === "active" ? "bg-green-400" : "bg-yellow-400"}`} />
                <span className="text-xs text-secondary">{currentBot.status}</span>
              </div>
            </button>
          </div>
          {botMenuOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setBotMenuOpen(false)} />
              <div className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-black/10 bg-white shadow-xl">
                {bots.map((bot) => (
                  <Link
                    key={bot.id}
                    href={buildBotChatHref(bot.id)}
                    onClick={() => {
                      setBotMenuOpen(false);
                      onMobileClose();
                    }}
                    className={`block px-3 py-2.5 text-sm transition-colors hover:bg-black/[0.04] ${
                      bot.id === currentBotId ? "bg-primary/[0.06] font-semibold text-primary-light" : "text-foreground"
                    }`}
                  >
                    <span className="block truncate">{bot.name}</span>
                    <span className="mt-0.5 block text-xs text-secondary">{bot.status}</span>
                  </Link>
                ))}
                {canAddBot && (
                  <>
                    <div className="border-t border-black/[0.06]" />
                    <button
                      type="button"
                      onClick={handleAddBot}
                      className="block w-full px-3 py-2.5 text-left text-sm font-semibold text-primary-light transition-colors hover:bg-black/[0.04]"
                    >
                      + New Bot
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div
        className="border-b border-black/8 bg-background px-3 py-2 md:bg-gray-50"
        data-chat-sidebar-edit-bar="true"
      >
        <div className="flex items-center justify-end gap-1.5">
          {editing && (
            <button
              onClick={handleCancel}
              className="px-2.5 py-1 rounded-lg bg-black/5 border border-black/8 text-xs font-semibold text-secondary hover:bg-black/10 transition-colors cursor-pointer"
            >
              Cancel
            </button>
          )}
          <button
            onClick={editing ? handleDone : onToggleEdit}
            className="px-2.5 py-1 rounded-lg bg-black/5 border border-black/8 text-xs font-semibold text-primary-light hover:bg-black/10 transition-colors cursor-pointer"
          >
            {editing ? "Done" : "Edit"}
          </button>
        </div>
      </div>

      {/* Channels */}
      <nav className="flex-1 min-h-0 p-3 overflow-y-auto" data-chat-sidebar-channel-nav="true">
        {editing ? (
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={flatIds} strategy={verticalListSortingStrategy}>
              <div className="space-y-0.5">
                {flatItems.map((item) => {
                  if (item.type === "header") {
                    const isCustom = !DEFAULT_CATEGORIES.includes(item.title) && item.title !== "Other";
                    return (
                      <SortableHeader
                        key={item.key}
                        id={item.key}
                        title={item.title}
                        localizedTitle={localizeCategory(item.title, locale)}
                        isCustom={isCustom}
                        isRenaming={renamingKey === item.key}
                        renameValue={renameValue}
                        onDeleteCategory={onDeleteCategory}
                        onStartRename={handleStartRename}
                        onRenameChange={setRenameValue}
                        onRenameSubmit={handleRenameSubmit}
                        onRenameCancel={handleRenameCancel}
                      />
                    );
                  }
                  const isCustomChannel = !DEFAULT_CHANNELS.includes(item.channel.name);
                  const canDelete = true;
                  const localizedChannel = { ...item.channel, display_name: localizeChannel(item.channel.name, item.channel.display_name, locale) };
                  return (
                    <SortableChannel
                      key={item.key}
                      id={item.key}
                      channel={localizedChannel}
                      isActive={activeChannel === item.channel.name}
                      isCustom={isCustomChannel}
                      canDelete={canDelete}
                      isRenaming={renamingKey === item.key}
                      renameValue={renameValue}
                      onStartRename={handleStartRename}
                      onRenameChange={setRenameValue}
                      onRenameSubmit={handleRenameSubmit}
                      onRenameCancel={handleRenameCancel}
                      onDelete={onDeleteChannel}
                    />
                  );
                })}
              </div>
            </SortableContext>
          </DndContext>
        ) : (
          <div className="space-y-3">
            {grouped.map(({ title, channels: chs }) => (
              <div key={title}>
                <div className="px-2 mb-1">
                  <h3 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{localizeCategory(title, locale)}</h3>
                </div>
                <div className="space-y-0.5">
                  {chs.map((ch) => {
                    const unread = activeChannel !== ch.name && useChatStore.getState().hasUnread(ch.name);
                    return (
                      <button
                        key={ch.id}
                        onClick={() => handleChannelClick(ch.name)}
                        onContextMenu={(e) => handleContextMenu(e, ch.name)}
                        className={`w-full text-left px-2 py-1.5 rounded-lg text-sm transition-colors cursor-pointer flex items-center gap-1.5 ${
                          activeChannel === ch.name
                            ? "bg-primary/10 text-primary-light"
                            : unread
                              ? "text-foreground font-semibold hover:bg-black/5"
                              : "text-secondary hover:text-foreground hover:bg-black/5"
                        }`}
                      >
                        <span className="min-w-0 flex-1 truncate">
                          # {formatChannelBaseLabel({
                            ...ch,
                            display_name: localizeChannel(ch.name, ch.display_name, locale),
                          })}
                        </span>
                        <MemoryModeBadge mode={ch.memory_mode} active={activeChannel === ch.name} />
                        {unread && (
                          <span className="w-2 h-2 rounded-full bg-primary shrink-0" />
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Unified + button */}
        <div className="relative mt-3">
          <button
            onClick={() => {
              if (editing) {
                setShowAddMenu(!showAddMenu);
              } else {
                setShowNewChannel(true);
              }
            }}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-secondary/60 hover:text-foreground hover:bg-black/5 transition-colors cursor-pointer"
          >
            <span className="text-base leading-none">+</span>
            {editing ? "Add..." : "Add Channel"}
          </button>
          {showAddMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowAddMenu(false)} />
              <div className="absolute left-0 bottom-full mb-1 bg-white border border-black/10 rounded-lg shadow-xl py-1 min-w-[160px] z-50">
                <button
                  onClick={() => { setShowAddMenu(false); setShowNewChannel(true); }}
                  className="w-full text-left px-3 py-2 text-sm text-foreground hover:bg-black/5 transition-colors cursor-pointer flex items-center gap-2"
                >
                  <span className="text-secondary/60">#</span> Channel
                </button>
                <button
                  onClick={() => { setShowAddMenu(false); setShowNewCategory(true); }}
                  className="w-full text-left px-3 py-2 text-sm text-foreground hover:bg-black/5 transition-colors cursor-pointer flex items-center gap-2"
                >
                  <svg className="w-3.5 h-3.5 text-secondary/60" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                  </svg>
                  Category
                </button>
              </div>
            </>
          )}
        </div>
      </nav>

      {/* Footer */}
      <div className="p-3 border-t border-black/8 space-y-1">
        <button
          onClick={onRefreshChannels}
          disabled={refreshing}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-secondary hover:text-foreground hover:bg-black/5 transition-colors cursor-pointer disabled:opacity-50"
        >
          <svg className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M21.015 4.356v4.992" />
          </svg>
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
        <Link
          href={currentBotId ? `/dashboard/${currentBotId}/overview` : "/dashboard"}
          className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-secondary hover:text-foreground hover:bg-black/5 transition-colors"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
          </svg>
          Dashboard
        </Link>
      </div>
    </>
  );

  return (
    <>
      {/* Desktop: always visible, in normal document flow */}
      <div className="hidden md:block w-64 h-screen border-r border-gray-200 bg-gray-50/80 shrink-0">
        <div className="h-full flex flex-col">
          {sidebarInner}
        </div>
      </div>

      {/* Mobile: backdrop */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden" onClick={onMobileClose} />
      )}

      {/* Mobile: drawer */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-72 bg-background border-r border-black/8 flex flex-col transform transition-transform duration-300 ease-in-out md:hidden ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {sidebarInner}
      </div>

      {/* Context menu */}
      {contextMenu && (
        <div
          className="fixed inset-0 z-50"
          onClick={() => setContextMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setContextMenu(null); }}
        >
          <div
            className="absolute bg-white border border-black/10 rounded-lg shadow-xl py-1 min-w-[140px]"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              onClick={() => { onDeleteChannel(contextMenu.channelName); setContextMenu(null); }}
              className="w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-black/5 transition-colors cursor-pointer"
            >
              Delete #{contextMenu.channelName}
            </button>
          </div>
        </div>
      )}

      {/* New Channel Modal */}
      {showNewChannel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => setShowNewChannel(false)}>
          <div className="bg-white border border-black/10 rounded-2xl p-6 w-80 max-w-[calc(100vw-2rem)] shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-bold text-foreground mb-4">New Channel</h3>
            <input
              type="text"
              value={newChannelName}
              onChange={(e) => setNewChannelName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateChannel();
                if (e.key === "Escape") {
                  setNewChannelMemoryMode("normal");
                  setShowNewChannel(false);
                }
              }}
              placeholder="Channel name (e.g. research)"
              className="w-full bg-black/5 border border-black/8 rounded-xl px-4 py-3 text-sm text-foreground placeholder-secondary focus:outline-none focus:border-primary/50 mb-4"
              autoFocus
            />
            <div className="mb-4 space-y-1.5">
              {([
                ["normal", "Normal memory"],
                ["read_only", "Read-only memory"],
                ["incognito", "No memory"],
              ] as const).map(([mode, label]) => (
                <label key={mode} className="flex items-center gap-2 rounded-xl border border-black/8 bg-black/[0.03] px-3 py-2.5 text-sm text-foreground">
                  <input
                    type="radio"
                    name="new-channel-memory-mode"
                    checked={newChannelMemoryMode === mode}
                    onChange={() => setNewChannelMemoryMode(mode)}
                    className="h-4 w-4 border-black/20 accent-primary"
                  />
                  <span className="min-w-0 flex-1">{label}</span>
                  <MemoryModeIcon mode={mode} />
                </label>
              ))}
            </div>
            <div className="flex gap-3">
              <button onClick={() => { setNewChannelName(""); setNewChannelMemoryMode("normal"); setShowNewChannel(false); }} className="flex-1 py-2.5 rounded-xl border border-black/8 text-sm text-secondary hover:bg-black/5 transition-colors cursor-pointer">Cancel</button>
              <button onClick={handleCreateChannel} className="flex-1 py-2.5 rounded-xl bg-primary text-sm text-white font-medium hover:bg-primary/80 transition-colors cursor-pointer">Create</button>
            </div>
          </div>
        </div>
      )}

      {/* New Category Modal */}
      {showNewCategory && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => setShowNewCategory(false)}>
          <div className="bg-white border border-black/10 rounded-2xl p-6 w-80 max-w-[calc(100vw-2rem)] shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-bold text-foreground mb-4">New Category</h3>
            <input
              type="text"
              value={newCategoryName}
              onChange={(e) => setNewCategoryName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateCategory();
                if (e.key === "Escape") setShowNewCategory(false);
              }}
              placeholder="Category name (e.g. Hobbies)"
              className="w-full bg-black/5 border border-black/8 rounded-xl px-4 py-3 text-sm text-foreground placeholder-secondary focus:outline-none focus:border-primary/50 mb-4"
              autoFocus
            />
            <div className="flex gap-3">
              <button onClick={() => { setNewCategoryName(""); setShowNewCategory(false); }} className="flex-1 py-2.5 rounded-xl border border-black/8 text-sm text-secondary hover:bg-black/5 transition-colors cursor-pointer">Cancel</button>
              <button onClick={handleCreateCategory} className="flex-1 py-2.5 rounded-xl bg-primary text-sm text-white font-medium hover:bg-primary/80 transition-colors cursor-pointer">Create</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
