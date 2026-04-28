/**
 * FD-based fs-safe wrappers (§15.2) — defend FileRead / FileWrite /
 * FileEdit against symlink-swap TOCTOU where a pre-open path check
 * passes but an attacker swaps a symlink between the check and the
 * actual `fs.readFile` / `fs.writeFile`.
 *
 * Strategy:
 *   1. Pre-open canonical-path check (cheap, catches the common case).
 *   2. Open the file → obtain an FD.
 *   3. Re-derive the FD's true path via `realpath(/proc/self/fd/N)`
 *      on linux, or `fs.realpath(origPath)` as a best-effort fallback
 *      on platforms without `/proc`.
 *   4. If the post-open path is no longer under `allowedRoot`, close
 *      the handle and throw FsSafeEscape.
 *   5. Only then run the IO op on the FD.
 *
 * Design reference:
 *   docs/plans/2026-04-19-clawy-core-agent-design.md §15.2
 */

import fs from "node:fs/promises";
import path from "node:path";
import { constants as fsConstants } from "node:fs";

/** Raised when a path escapes allowedRoot at any check. */
export class FsSafeEscape extends Error {
  constructor(
    message: string,
    readonly attemptedPath: string,
    readonly resolvedPath: string,
    readonly allowedRoot: string,
  ) {
    super(message);
    this.name = "FsSafeEscape";
  }
}

export function isFsSafeEscape(err: unknown): err is FsSafeEscape {
  return err instanceof FsSafeEscape || (err as { name?: string })?.name === "FsSafeEscape";
}

/**
 * True path-prefix guard. Both arguments must be absolute. Exposes the
 * sep-boundary requirement so `/root/a` does not match `/root/ab`.
 */
export function isUnderRoot(absPath: string, absRoot: string): boolean {
  if (absPath === absRoot) return true;
  return absPath.startsWith(absRoot + path.sep);
}

/**
 * Re-derive the true path of an open FD. Returns null if we cannot
 * determine it on the current platform — callers treat null as "trust
 * the pre-open check only" (documented fallback).
 */
export async function fdRealPath(fd: number): Promise<string | null> {
  // Linux: /proc/self/fd/<N> is a magic symlink to the FD's path.
  if (process.platform === "linux") {
    try {
      return await fs.realpath(`/proc/self/fd/${fd}`);
    } catch {
      return null;
    }
  }
  // Other platforms (darwin test env, BSDs): no magic path. Return
  // null; caller falls back to realpath on the input path, which is
  // TOCTOU-vulnerable but still blocks the static-escape case.
  return null;
}

/**
 * Open a file safely.
 *
 * Pre-open:  resolve path → assert under allowedRoot
 * Open:      fs.open(resolvedPath, flags, mode)
 * Post-open: fd realpath → assert under allowedRoot (linux only)
 *
 * Throws FsSafeEscape on any escape. Caller owns the returned handle
 * and MUST close it (or use the readSafe/writeSafe/appendSafe
 * wrappers which handle close automatically).
 */
export async function openSafe(
  userPath: string,
  flags: string | number,
  allowedRoot: string,
  mode?: number,
): Promise<fs.FileHandle> {
  // Canonicalise root via realpath so subsequent comparisons happen
  // in the same symlink-resolved namespace. Necessary on darwin where
  // /var → /private/var: `path.resolve(tmp)` stays as /var/... but
  // realpath of any file underneath returns /private/var/..., making
  // naive prefix checks fail. If realpath fails (root doesn't exist
  // yet — uncommon but possible in fresh-workspace tests) fall back
  // to the resolved-path form.
  let absRoot: string;
  try {
    absRoot = await fs.realpath(path.resolve(allowedRoot));
  } catch {
    absRoot = path.resolve(allowedRoot);
  }
  // Normalise + join under root, then resolve symlink components that
  // already exist. The pre-open check catches static escapes (absolute
  // path outside root, `../../etc/passwd`, already-pointing-out
  // symlinks).
  const normalised = path.normalize(userPath).replace(/^\/+/, "");
  const joined = path.join(absRoot, normalised);
  const absJoined = path.resolve(joined);
  if (!isUnderRoot(absJoined, absRoot)) {
    throw new FsSafeEscape(
      `path escapes allowed root (pre-open): ${userPath}`,
      userPath,
      absJoined,
      absRoot,
    );
  }

  // Pre-open realpath where the file already exists — catches pointing-
  // out symlinks without needing /proc.
  let preOpenReal: string | null = null;
  try {
    preOpenReal = await fs.realpath(absJoined);
    if (!isUnderRoot(preOpenReal, absRoot)) {
      throw new FsSafeEscape(
        `path escapes allowed root (pre-open realpath): ${userPath}`,
        userPath,
        preOpenReal,
        absRoot,
      );
    }
  } catch (err) {
    // ENOENT is expected for writeSafe on new files — pass through.
    if ((err as NodeJS.ErrnoException)?.code !== "ENOENT") {
      if (isFsSafeEscape(err)) throw err;
      // Unexpected realpath failure: don't block, defer to post-open.
      preOpenReal = null;
    }
  }

  const handle = await fs.open(absJoined, flags, mode);

  // Post-open: re-derive path from FD and re-validate.
  try {
    const postOpenReal = await fdRealPath(handle.fd);
    if (postOpenReal !== null && !isUnderRoot(postOpenReal, absRoot)) {
      await handle.close().catch(() => undefined);
      throw new FsSafeEscape(
        `path escapes allowed root (post-open FD realpath): ${userPath}`,
        userPath,
        postOpenReal,
        absRoot,
      );
    }
    // Linux only: detect swap — pre-open and post-open disagreeing is
    // itself suspicious even if both are inside root.
    if (
      postOpenReal !== null &&
      preOpenReal !== null &&
      postOpenReal !== preOpenReal
    ) {
      await handle.close().catch(() => undefined);
      throw new FsSafeEscape(
        `path swap detected (pre=${preOpenReal} post=${postOpenReal}): ${userPath}`,
        userPath,
        postOpenReal,
        absRoot,
      );
    }
  } catch (err) {
    if (isFsSafeEscape(err)) throw err;
    await handle.close().catch(() => undefined);
    throw err;
  }

  return handle;
}

/** Read the full file as utf8 text, fs-safely. */
export async function readSafe(
  userPath: string,
  allowedRoot: string,
): Promise<string> {
  const handle = await openSafe(userPath, fsConstants.O_RDONLY, allowedRoot);
  try {
    return await handle.readFile("utf8");
  } finally {
    await handle.close().catch(() => undefined);
  }
}

/** Write the file (O_CREAT | O_WRONLY | O_TRUNC), fs-safely. */
export async function writeSafe(
  userPath: string,
  data: string | Buffer,
  allowedRoot: string,
  mode = 0o644,
): Promise<void> {
  const handle = await openSafe(
    userPath,
    // eslint-disable-next-line no-bitwise
    fsConstants.O_CREAT | fsConstants.O_WRONLY | fsConstants.O_TRUNC,
    allowedRoot,
    mode,
  );
  try {
    await handle.writeFile(data);
  } finally {
    await handle.close().catch(() => undefined);
  }
}

/** Append to the file (O_CREAT | O_WRONLY | O_APPEND), fs-safely. */
export async function appendSafe(
  userPath: string,
  data: string | Buffer,
  allowedRoot: string,
  mode = 0o644,
): Promise<void> {
  const handle = await openSafe(
    userPath,
    // eslint-disable-next-line no-bitwise
    fsConstants.O_CREAT | fsConstants.O_WRONLY | fsConstants.O_APPEND,
    allowedRoot,
    mode,
  );
  try {
    await handle.appendFile(data);
  } finally {
    await handle.close().catch(() => undefined);
  }
}

/**
 * Stat via an open FD. Returns null if the path doesn't exist (ENOENT).
 * Used by FileRead to check `isFile()` without racing the read.
 */
export async function statSafe(
  userPath: string,
  allowedRoot: string,
): Promise<import("node:fs").Stats | null> {
  let handle: fs.FileHandle;
  try {
    handle = await openSafe(userPath, fsConstants.O_RDONLY, allowedRoot);
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "ENOENT") return null;
    throw err;
  }
  try {
    return await handle.stat();
  } finally {
    await handle.close().catch(() => undefined);
  }
}
