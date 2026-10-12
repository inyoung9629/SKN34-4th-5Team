import { CommunityBoard } from "@/components/community-board";

export const metadata = { title: "팀 게시판" };
export default async function Page({ searchParams }: { searchParams: Promise<{ team?: string | string[]; post?: string | string[]; category?: string | string[] }> }) {
  const { team, post, category } = await searchParams;
  return <CommunityBoard section="teams" teamCode={typeof team === "string" ? team : ""} postId={typeof post === "string" ? post : ""} category={typeof category === "string" ? category : ""} />;
}
