"use client";

import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";

interface ViewAsContextValue {
  /** The user ID being viewed, or null if not in view-as mode */
  viewAsUserId: string | null;
  /** Display info for the banner */
  viewAsDisplayName: string | null;
}

const ViewAsContext = createContext<ViewAsContextValue>({
  viewAsUserId: null,
  viewAsDisplayName: null,
});

interface ViewAsProviderProps {
  viewAsUserId: string | null;
  viewAsDisplayName: string | null;
  children: ReactNode;
}

export function ViewAsProvider({ viewAsUserId, viewAsDisplayName, children }: ViewAsProviderProps): ReactNode {
  const value = useMemo(
    () => ({ viewAsUserId, viewAsDisplayName }),
    [viewAsUserId, viewAsDisplayName],
  );
  return <ViewAsContext.Provider value={value}>{children}</ViewAsContext.Provider>;
}

export function useViewAs(): ViewAsContextValue {
  return useContext(ViewAsContext);
}
