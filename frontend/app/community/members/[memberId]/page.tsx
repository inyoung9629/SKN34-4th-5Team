import { notFound } from "next/navigation";
import { CommunityMemberPage } from "@/components/community-member-page";
import { parseMemberActivityQuery } from "@/lib/member-return-path";

export const metadata = { title: "멤버 활동", robots: { index: false, follow: false } };

export default async function Page({ params, searchParams }: {
  params: Promise<{ memberId: string }>;
  searchParams: Promise<{ tab?: string | string[]; page?: string | string[] }>;
}) {
  const [route, query] = await Promise.all([params, searchParams]);
  const memberId = Number(route.memberId);
  if (!/^[1-9]\d*$/.test(route.memberId) || !Number.isSafeInteger(memberId)) notFound();
  const { tab, page } = parseMemberActivityQuery(query);
  return <CommunityMemberPage memberId={memberId} tab={tab} page={page} />;
}
