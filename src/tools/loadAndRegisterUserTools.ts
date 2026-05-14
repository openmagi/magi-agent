/**
 * loadAndRegisterUserTools — loads user-authored tools via ToolLoader,
 * applies MagiConfig overrides (permission, timeout, disable_builtin),
 * and registers them into the ToolRegistry.
 *
 * Call this after registering builtin tools so custom tools can
 * override or extend builtin behavior.
 */

import type { ToolRegistry } from "./ToolRegistry.js";
import { loadUserTools } from "./ToolLoader.js";
import type { Tool, PermissionClass } from "../Tool.js";
import type { MagiConfigData } from "../config/MagiConfig.js";

export interface UserToolRegistrationResult {
  registered: number;
  skipped: string[];
  warnings: string[];
}

const VALID_PERMISSIONS = new Set<string>([
  "read",
  "write",
  "execute",
  "net",
  "meta",
]);

/**
 * Load user tools from the filesystem and register them into the
 * provided ToolRegistry, applying config overrides.
 */
export async function loadAndRegisterUserTools(
  registry: ToolRegistry,
  config: MagiConfigData,
  workspaceRoot?: string,
): Promise<UserToolRegistrationResult> {
  // 1. Apply disable_builtin — unregister named builtin tools
  for (const name of config.tools.disable_builtin) {
    registry.unregister(name);
  }

  // 2. Load user tools from filesystem
  const { tools, warnings } = await loadUserTools({
    directory: config.tools.directory,
    globalDirectory: config.tools.global_directory,
    workspaceRoot,
  });

  const skipped: string[] = [];
  let registered = 0;

  for (const tool of tools) {
    const override = config.tools.overrides[tool.name];

    // Check if tool is disabled via override
    if (override?.enabled === false) {
      skipped.push(tool.name);
      continue;
    }

    // Apply overrides
    let finalTool: Tool = tool;
    if (override) {
      const overrideFields: Partial<Tool> = {};
      if (
        override.permission &&
        VALID_PERMISSIONS.has(override.permission)
      ) {
        overrideFields.permission = override.permission as PermissionClass;
      }
      if (Object.keys(overrideFields).length > 0) {
        finalTool = { ...tool, ...overrideFields };
      }
    }

    // Register — use replace to allow overriding builtins
    registry.replace(finalTool);
    registered++;
  }

  return { registered, skipped, warnings };
}
