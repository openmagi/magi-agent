export function useRouter() {
  return {
    push(href: string) {
      window.history.pushState({}, "", href);
      window.dispatchEvent(new PopStateEvent("popstate"));
    },
    replace(href: string) {
      window.history.replaceState({}, "", href);
      window.dispatchEvent(new PopStateEvent("popstate"));
    },
    refresh() {
      window.location.reload();
    },
  };
}

export function useParams(): Record<string, string> {
  return { botId: "local" };
}
