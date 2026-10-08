import type { CommunityPostDto } from "./api/content";

export const teamBoards = [
  { code: "LG", name: "LG 트윈스", shortName: "LG", stadium: "잠실야구장" },
  { code: "HH", name: "한화 이글스", shortName: "한화", stadium: "대전 한화생명 볼파크" },
  { code: "SK", name: "SSG 랜더스", shortName: "SSG", stadium: "인천 SSG 랜더스필드" },
  { code: "SS", name: "삼성 라이온즈", shortName: "삼성", stadium: "대구 삼성 라이온즈 파크" },
  { code: "NC", name: "NC 다이노스", shortName: "NC", stadium: "창원 NC 파크" },
  { code: "KT", name: "KT 위즈", shortName: "KT", stadium: "수원 KT 위즈 파크" },
  { code: "LT", name: "롯데 자이언츠", shortName: "롯데", stadium: "사직야구장" },
  { code: "HT", name: "KIA 타이거즈", shortName: "KIA", stadium: "광주-KIA 챔피언스 필드" },
  { code: "OB", name: "두산 베어스", shortName: "두산", stadium: "잠실야구장" },
  { code: "WO", name: "키움 히어로즈", shortName: "키움", stadium: "고척스카이돔" },
] as const;

export type TeamCommunityPost = CommunityPostDto;

export function getTeamBoard(code: string) {
  return teamBoards.find(team => team.code === code.toUpperCase());
}

export function getTeamBoardHref(code?: string, postId?: string) {
  const params = new URLSearchParams();
  if (code && getTeamBoard(code)) params.set("team", code.toUpperCase());
  if (postId) params.set("post", postId);
  return `/community/teams${params.size ? `?${params.toString()}` : ""}`;
}

export function getCommunityPostHref(post: { id: string; board: "free" | "teams"; teamCode: string }) {
  return post.board === "free"
    ? `/community?post=${encodeURIComponent(post.id)}`
    : getTeamBoardHref(post.teamCode, post.id);
}

export function getCommunityWriteHref(board: "free" | "teams", code = "") {
  const params = new URLSearchParams({ board });
  const team = board === "teams" ? getTeamBoard(code) : undefined;
  if (team) params.set("team", team.code);
  return `/community/write?${params.toString()}`;
}
