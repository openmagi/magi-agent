import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_CHANNEL_MODEL_SELECTION,
  channelModelSelectionFromChannel,
  channelModelSelectionToRuntimeModel,
  getChannelModelSelection,
  setChannelModelSelection,
} from "./channel-model-selection";

describe("channel model selection", () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    storage.clear();
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => storage.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        storage.set(key, value);
      }),
      removeItem: vi.fn((key: string) => {
        storage.delete(key);
      }),
      clear: vi.fn(() => {
        storage.clear();
      }),
    });
    localStorage.clear();
  });

  it("stores model selections independently per bot channel", () => {
    setChannelModelSelection("bot-1", "general", {
      modelSelection: "opus",
      routerType: "standard",
    });
    setChannelModelSelection("bot-1", "research", {
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
    });

    expect(getChannelModelSelection("bot-1", "general", DEFAULT_CHANNEL_MODEL_SELECTION)).toEqual({
      modelSelection: "opus",
      routerType: "standard",
    });
    expect(getChannelModelSelection("bot-1", "research", DEFAULT_CHANNEL_MODEL_SELECTION)).toEqual({
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
    });
    expect(getChannelModelSelection("bot-1", "new-channel", DEFAULT_CHANNEL_MODEL_SELECTION)).toEqual(
      DEFAULT_CHANNEL_MODEL_SELECTION,
    );
  });

  it("maps stored UI selections to runtime model overrides", () => {
    expect(channelModelSelectionToRuntimeModel({
      modelSelection: "clawy_smart_routing",
      routerType: "standard",
    })).toBe("clawy-smart-router/auto");
    expect(channelModelSelectionToRuntimeModel({
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
    })).toBe("big-dic-router/auto");
    expect(channelModelSelectionToRuntimeModel({
      modelSelection: "gpt_5_5_pro",
      routerType: "standard",
    })).toBe("openai/gpt-5.5-pro");
    expect(channelModelSelectionToRuntimeModel({
      modelSelection: "opus",
      routerType: "standard",
    })).toBe("anthropic/claude-opus-4-6");
  });

  it("restores channel model selection from server channel preferences", () => {
    expect(channelModelSelectionFromChannel({
      model_selection: "kimi_k2_5",
      router_type: "standard",
    })).toEqual({
      modelSelection: "kimi_k2_5",
      routerType: "standard",
    });

    expect(channelModelSelectionFromChannel({
      model_selection: "clawy_smart_routing",
      router_type: "big_dic",
    })).toEqual({
      modelSelection: "clawy_smart_routing",
      routerType: "big_dic",
    });

    expect(channelModelSelectionFromChannel({ model_selection: null, router_type: null })).toBeNull();
  });
});
