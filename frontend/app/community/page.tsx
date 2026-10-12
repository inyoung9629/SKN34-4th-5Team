import { CommunityBoard } from "@/components/community-board";

export const metadata = { title: "자유 게시판" };

export default async function Page({ searchParams }: { searchParams: Promise<{ post?: string | string[]; category?: string | string[] }> }) {
  const { post, category } = await searchParams;
  return <CommunityBoard section="free" postId={typeof post === "string" ? post : ""} category={typeof category === "string" ? category : ""} />;
}
