interface PageFileDropEvent {
  readonly defaultPrevented: boolean;
  preventDefault: () => void;
}

export function shouldHandlePageFileDrop(event: PageFileDropEvent): boolean {
  const handledByDescendant = event.defaultPrevented;
  event.preventDefault();
  return !handledByDescendant;
}
