import { ApiError, apiRequest } from "./api/client";
import type { CommunityMemberSummaryDto, CommunityMemberPostDto, CommunityMemberCommentDto, CommunityMemberPageDto } from "./api/content";
import { memberFetch } from "./member-auth-request";
import { getTeamBoard } from "./team-community";

type Validator<T> = (value: unknown) => value is T;
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const positive = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
const text = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const date = (v: unknown): v is string => typeof v === "string" && Number.isFinite(Date.parse(v));
const nullableText = (v: unknown) => v === null || typeof v === "string";
const board = (v: Record<string, unknown>) => v.board === "free" ? v.teamCode === "" : v.board === "teams" && typeof v.teamCode === "string" && Boolean(getTeamBoard(v.teamCode));
const isMember: Validator<CommunityMemberSummaryDto> = (v): v is CommunityMemberSummaryDto => record(v) && positive(v.id) && text(v.nickname) && typeof v.activityVisible === "boolean";
const isPost: Validator<CommunityMemberPostDto> = (v): v is CommunityMemberPostDto => record(v) && text(v.id) && text(v.title) && board(v) && (v.createdAt === null || date(v.createdAt));
const isComment: Validator<CommunityMemberCommentDto> = (v): v is CommunityMemberCommentDto => record(v) && positive(v.id) && typeof v.content === "string" && date(v.createdAt) && text(v.postId) && text(v.postTitle) && board(v);

function paginated<T>(validate: Validator<T>): Validator<CommunityMemberPageDto<T>> {
  return (v): v is CommunityMemberPageDto<T> => record(v) && typeof v.count === "number" && Number.isSafeInteger(v.count) && v.count >= 0
    && nullableText(v.next) && nullableText(v.previous) && Array.isArray(v.results) && v.results.every(validate) && v.count >= v.results.length;
}

function validateIds(memberId: number, page = 1) {
  if (!positive(memberId)) throw new ApiError("올바른 회원 번호가 아닙니다.", 400);
  if (!positive(page) || page > 2_147_483_647) throw new ApiError("올바른 페이지 번호가 아닙니다.", 400);
}

async function request<T>(path: string, validate: Validator<T>, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(15_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  requestSignal.throwIfAborted();
  const data = await apiRequest<unknown>(path, { cache: "no-store", signal: requestSignal }, memberFetch);
  requestSignal.throwIfAborted();
  if (!validate(data)) throw new ApiError("멤버 활동 응답 형식이 올바르지 않습니다.", 502);
  return data;
}

export async function fetchCommunityMember(memberId: number, signal?: AbortSignal) {
  validateIds(memberId);
  const member = await request(`/api/v1/auth/users/${memberId}/public/`, isMember, signal);
  if (member.id !== memberId) throw new ApiError("조회한 회원 정보가 일치하지 않습니다.", 502);
  return member;
}

export function fetchCommunityMemberPosts(memberId: number, page = 1, signal?: AbortSignal) {
  validateIds(memberId, page);
  return request(`/api/v1/community/posts/?${new URLSearchParams({ author_id: String(memberId), page: String(page), page_size: "20" })}`, paginated(isPost), signal);
}

export function fetchCommunityMemberComments(memberId: number, page = 1, signal?: AbortSignal) {
  validateIds(memberId, page);
  return request(`/api/v1/community/comments/?${new URLSearchParams({ author_id: String(memberId), page: String(page), page_size: "20" })}`, paginated(isComment), signal);
}
