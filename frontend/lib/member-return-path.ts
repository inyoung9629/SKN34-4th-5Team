const LOCAL_ORIGIN = "https://local.invalid";

export type ActivityTab = "posts" | "comments";

export function parseMemberActivityQuery(query: { tab?: string | string[] | null; page?: string | string[] | null }) {
  const tab: ActivityTab = query.tab === "comments" ? "comments" : "posts";
  const rawPage = typeof query.page === "string" ? query.page : "1";
  const parsed = Number(rawPage);
  const page = /^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(parsed) && parsed <= 2_147_483_647 ? parsed : 1;
  return { tab, page };
}

export function activityHref(memberId: number, tab: ActivityTab = "posts", page = 1) {
  return `/community/members/${memberId}?${new URLSearchParams({ tab, page: String(page) })}`;
}

export function memberActivityLoginHref(memberId: number, tab: ActivityTab = "posts", page = 1) {
  return `/login?${new URLSearchParams({ next: activityHref(memberId, tab, page) })}`;
}

export function safeMemberReturnPath(raw: string | null): string | null {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return null;
  try {
    const url = new URL(raw, LOCAL_ORIGIN);
    if (url.origin !== LOCAL_ORIGIN) return null;
    const match = /^\/community\/members\/([1-9]\d*)\/?$/.exec(url.pathname);
    if (!match || !Number.isSafeInteger(Number(match[1]))) return null;
    const { tab, page } = parseMemberActivityQuery({
      tab: url.searchParams.getAll("tab").length > 1 ? url.searchParams.getAll("tab") : url.searchParams.get("tab"),
      page: url.searchParams.getAll("page").length > 1 ? url.searchParams.getAll("page") : url.searchParams.get("page"),
    });
    return activityHref(Number(match[1]), tab, page);
  } catch {
    return null;
  }
}
