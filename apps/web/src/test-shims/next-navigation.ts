export function useRouter() {
  return {
    push(_href: string) {},
    replace(_href: string) {},
    refresh() {},
  };
}

export function useParams(): Record<string, string> {
  return { botId: "local" };
}

export function usePathname(): string {
  return "/dashboard/local/chat";
}

export function useSearchParams() {
  return new URLSearchParams();
}
