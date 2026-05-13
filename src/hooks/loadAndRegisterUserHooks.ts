/**
 * loadAndRegisterUserHooks — loads user-authored hooks via HookLoader,
 * applies MagiConfig overrides (disable_builtin, priority, timeout,
 * blocking), and registers them into the HookRegistry.
 *
 * Call this after `registerBuiltinHooks()` so user hooks can override
 * or extend builtin behavior.
 */

import type { HookRegistry } from "./HookRegistry.js";
import { loadUserHooks } from "./HookLoader.js";
import type { RegisteredHook } from "./types.js";
import type { MagiConfigData } from "../config/MagiConfig.js";

export interface UserHookRegistrationResult {
  registered: number;
  skipped: string[];
  warnings: string[];
}

/**
 * Load user hooks from the filesystem and register them into the
 * provided HookRegistry, applying config overrides.
 *
 * The `disableBuiltin` list from config is returned so the caller can
 * pass it to `registerBuiltinHooks({ disabled })`.
 */
export async function loadAndRegisterUserHooks(
  registry: HookRegistry,
  config: MagiConfigData,
  workspaceRoot?: string,
): Promise<UserHookRegistrationResult> {
  const { hooks, warnings } = await loadUserHooks({
    directory: config.hooks.directory,
    globalDirectory: config.hooks.global_directory,
    workspaceRoot,
  });

  const skipped: string[] = [];
  let registered = 0;

  for (const hook of hooks) {
    const override = config.hooks.overrides[hook.name];

    // Check if hook is disabled via override
    if (override?.enabled === false) {
      skipped.push(hook.name);
      continue;
    }

    // Apply overrides to a copy of the hook
    const finalHook: RegisteredHook = {
      ...hook,
      ...(override?.priority !== undefined
        ? { priority: override.priority }
        : {}),
      ...(override?.blocking !== undefined
        ? { blocking: override.blocking }
        : {}),
      ...(override?.timeoutMs !== undefined
        ? { timeoutMs: override.timeoutMs }
        : {}),
    };

    registry.register(finalHook);
    registered++;
  }

  return { registered, skipped, warnings };
}

/**
 * Extract the `disable_builtin` list from MagiConfig so callers can
 * pass it to `registerBuiltinHooks({ disabled })`.
 */
export function getDisabledBuiltins(config: MagiConfigData): string[] {
  return config.hooks.disable_builtin;
}
