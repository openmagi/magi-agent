import { describe, expect, it, vi } from "vitest";
import { DEFAULT_CHAT_UPLOAD_COLLECTION, ensurePersonalDefaultCollection } from "./default-collection";

describe("ensurePersonalDefaultCollection", () => {
  it("returns an existing Downloads collection without inserting a duplicate", async () => {
    const maybeSingle = vi.fn(async () => ({
      data: { id: "col-1", name: DEFAULT_CHAT_UPLOAD_COLLECTION },
      error: null,
    }));
    const insert = vi.fn();

    const supabase = {
      from: vi.fn(() => ({
        select: vi.fn(() => ({
          eq: vi.fn(() => ({
            eq: vi.fn(() => ({
              maybeSingle,
            })),
          })),
        })),
        insert,
      })),
    };

    const collection = await ensurePersonalDefaultCollection(supabase as never, "bot-1");

    expect(collection).toEqual({ id: "col-1", name: DEFAULT_CHAT_UPLOAD_COLLECTION });
    expect(insert).not.toHaveBeenCalled();
  });
});
