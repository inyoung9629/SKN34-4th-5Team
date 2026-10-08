"use client";

import { useEffect, useSyncExternalStore } from "react";
import { communityPostCategories } from "./community-post-category";
import type { CommunityCommentDto, CommunityPostWriteDto, CommunityReportResultDto, CommunityReportWriteDto, CommunityVoteStateDto } from "./api/content";
import { apiRequest } from "./api/client";
import { memberError, memberFetch } from "./member-auth-request";
import { richColors, richSizes, type RichContentDoc } from "./community-rich-content";
import { teamBoards, type TeamCommunityPost } from "./team-community";

type CommunityState = { posts: TeamCommunityPost[]; loading: boolean; error: string };
export type CommunityPostInput = CommunityPostWriteDto;
export type CommunityComment = CommunityCommentDto;
export type CommunityVoteState = CommunityVoteStateDto;
export type CommunityReportReason = CommunityReportWriteDto["reason"];

const listeners = new Set<() => void>();
const teamCodes = new Set(teamBoards.map(team => team.code));
const categories = new Set<string>(communityPostCategories);
const reportReasons = new Set<CommunityReportReason>(["spam", "abuse", "inappropriate", "privacy", "other"]);
let state: CommunityState = { posts: [], loading: true, error: "" };
let request: Promise<void> | undefined;
let requestGeneration = 0;

const isCount = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const isPositiveInteger = (value: unknown) => Number.isSafeInteger(value) && Number(value) > 0;
const timeoutSignal = () => AbortSignal.timeout(15000);
const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method, cache: "no-store", signal: timeoutSignal(),
  headers: { "Content-Type": "application/json" },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

function isPost(value: unknown): value is TeamCommunityPost {
  if (!value || typeof value !== "object") return false;
  const post = value as Record<string, unknown>;
  return typeof post.id === "string" && post.sourceId === post.id && /^\d{6}$/.test(String(post.postNumber))
    && (post.board === "free" || post.board === "teams")
    && typeof post.teamCode === "string" && (post.board === "free" ? post.teamCode === "" : teamCodes.has(post.teamCode as typeof teamBoards[number]["code"]))
    && ["author", "title", "content"].every(field => typeof post[field] === "string")
    && typeof post.category === "string" && categories.has(post.category)
    && (post.authorId === undefined || post.authorId === null || isPositiveInteger(post.authorId))
    && (post.createdAt === null || typeof post.createdAt === "string")
    && ["views", "recommendations", "commentCount"].every(field => isCount(post[field]))
    && (post.downvotes === undefined || isCount(post.downvotes))
    && typeof post.isSample === "boolean"
    && (post.contentDoc === undefined || post.contentDoc === null || isRichDoc(post.contentDoc));
}

function isRichDoc(value: unknown): value is RichContentDoc {
  if (!value || typeof value !== "object") return false;
  const doc = value as Record<string, unknown>;
  if (doc.version !== 1 || !Array.isArray(doc.blocks) || doc.blocks.length > 500) return false;
  return doc.blocks.every(block => {
    if (!block || typeof block !== "object") return false;
    const item = block as Record<string, unknown>;
    if (item.type === "image") return typeof item.id === "string" && /^[0-9a-f-]{36}$/i.test(item.id);
    if (item.type !== "paragraph" || !["left", "center", "right"].includes(String(item.align)) || !Array.isArray(item.runs)) return false;
    return item.runs.every(run => {
      if (!run || typeof run !== "object") return false;
      const text = run as Record<string, unknown>;
      return typeof text.text === "string" && ["sans", "serif", "mono"].includes(String(text.font))
        && richSizes.includes(text.size as typeof richSizes[number]) && richColors.includes(text.color as typeof richColors[number])
        && ["bold", "italic", "underline"].every(key => typeof text[key] === "boolean");
    });
  });
}

function isComment(value: unknown): value is CommunityComment {
  if (!value || typeof value !== "object") return false;
  const comment = value as Record<string, unknown>;
  return isPositiveInteger(comment.id) && typeof comment.postId === "string"
    && isPositiveInteger(comment.authorId)
    && ["author", "content", "createdAt", "updatedAt"].every(field => typeof comment[field] === "string");
}

function isVoteState(value: unknown): value is CommunityVoteState {
  if (!value || typeof value !== "object") return false;
  const vote = value as Record<string, unknown>;
  return (vote.vote === "up" || vote.vote === "down" || vote.vote === null) && isCount(vote.recommendations) && isCount(vote.downvotes);
}

function isReportResult(value: unknown): value is CommunityReportResultDto {
  if (!value || typeof value !== "object") return false;
  const report = value as Record<string, unknown>;
  return isPositiveInteger(report.id) && typeof report.created === "boolean";
}

function pathId(value: string, label: string) {
  if (typeof value !== "string" || !value || value.length > 40) throw new Error(`${label} 식별자가 올바르지 않아요.`);
  return encodeURIComponent(value);
}

function numericId(value: number, label: string) {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${label} 식별자가 올바르지 않아요.`);
  return value;
}

function content(value: string, label: string, maxLength: number) {
  if (typeof value !== "string") throw new Error(`${label}이 올바르지 않아요.`);
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label}을 입력해 주세요.`);
  if (trimmed.length > maxLength) throw new Error(`${label}은 ${maxLength}자 이하로 입력해 주세요.`);
  return trimmed;
}

function postInput(input: CommunityPostInput) {
  if (!input || typeof input !== "object" || typeof input.teamCode !== "string") throw new Error("게시글 입력이 올바르지 않아요.");
  if (input.board !== "free" && input.board !== "teams") throw new Error("게시판이 올바르지 않아요.");
  if (!categories.has(input.category)) throw new Error("게시글 분류가 올바르지 않아요.");
  const teamCode = input.teamCode.toUpperCase();
  if (input.board === "free" ? teamCode !== "" : !teamCodes.has(teamCode as typeof teamBoards[number]["code"])) throw new Error("게시판과 팀이 올바르지 않아요.");
  if (input.contentDoc && !isRichDoc(input.contentDoc)) throw new Error("본문 서식이 올바르지 않아요.");
  return { ...input, teamCode, title: content(input.title, "제목", 200), content: content(input.content, "본문", 20000) };
}

export async function uploadCommunityImage(file: File) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("JPG, PNG, WEBP 이미지만 올릴 수 있어요.");
  if (file.size > 5 * 1024 * 1024) throw new Error("이미지는 한 장에 5MB 이하로 올려 주세요.");
  const form = new FormData();
  form.append("image", file);
  const response = await memberFetch("/api/v1/community/images/", { method: "POST", body: form, signal: AbortSignal.timeout(60000) });
  if (!response.ok) throw await apiError(response, "이미지를 올리지 못했어요.");
  const data: unknown = await response.json().catch(() => null);
  if (!data || typeof data !== "object" || !/^[0-9a-f-]{36}$/i.test(String((data as Record<string, unknown>).id))) throw new Error("이미지 응답이 올바르지 않아요.");
  return (data as { id: string }).id;
}

async function apiError(response: Response, fallback: string) {
  const message = memberError(await response.json().catch(() => null), fallback).trim();
  return new Error(message.length <= 200 && /[가-힣]/.test(message) ? message : fallback);
}
function requestError(error: unknown, fallback: string) {
  if (error instanceof DOMException && (error.name === "AbortError" || error.name === "TimeoutError")) return new Error("요청 시간이 초과됐어요.");
  if (error instanceof Error && error.message.length <= 200 && /[가-힣]/.test(error.message)) return error;
  return new Error(fallback);
}

async function requestJson<T>(path: string, init: RequestInit, validate: (value: unknown) => value is T, fallback: string, authenticated = true): Promise<T> {
  try {
    const data = await apiRequest<unknown>(path, init, authenticated ? memberFetch : fetch);
    if (!validate(data)) throw new Error("서버 응답 형식이 올바르지 않아요.");
    return data;
  } catch (error) {
    throw requestError(error, fallback);
  }
}

async function requestVoid(path: string, init: RequestInit, fallback: string) {
  try {
    await apiRequest(path, init, memberFetch);
  } catch (error) {
    throw requestError(error, fallback);
  }
}

export async function fetchCommunityPosts(fetcher: typeof fetch = fetch): Promise<TeamCommunityPost[]> {
  try {
    const data = await apiRequest<unknown>("/api/v1/community/posts/", { cache: "no-store", signal: timeoutSignal() }, fetcher);
    if (!Array.isArray(data) || !data.every(isPost)) throw new Error("서버 응답 형식이 올바르지 않아요.");
    return data;
  } catch (error) {
    throw requestError(error, "게시글을 불러오지 못했어요.");
  }
}

function publish(next: CommunityState) {
  state = next;
  listeners.forEach(listener => listener());
}

function loadCommunityPosts(force = false) {
  if (!request || force) {
    const generation = ++requestGeneration;
    publish({ ...state, loading: true, error: "" });
    const current = fetchCommunityPosts()
      .then(posts => { if (generation === requestGeneration) publish({ posts, loading: false, error: "" }); })
      .catch(() => { if (generation === requestGeneration) publish({ ...state, loading: false, error: "게시글을 불러오지 못했어요." }); })
      .finally(() => { if (request === current) request = undefined; });
    request = current;
  }
  return request;
}

export function retryCommunityPosts() { return loadCommunityPosts(); }

const refreshCommunityPosts = () => loadCommunityPosts(true);

export function getCommunityPost(id: string) {
  return requestJson(`/api/v1/community/posts/${pathId(id, "게시글")}/`, { cache: "no-store", signal: timeoutSignal() }, isPost, "게시글을 불러오지 못했어요.", false);
}

export async function createCommunityPost(input: CommunityPostInput, idempotencyKey: string) {
  const key = content(idempotencyKey, "요청 식별자", 128);
  const post = await requestJson("/api/v1/community/posts/", { ...jsonInit("POST", postInput(input)), headers: { "Content-Type": "application/json", "Idempotency-Key": key } }, isPost, "게시글을 등록하지 못했어요.");
  await refreshCommunityPosts();
  return post;
}

export async function updateCommunityPost(id: string, input: CommunityPostInput) {
  const post = await requestJson(`/api/v1/community/posts/${pathId(id, "게시글")}/`, jsonInit("PATCH", postInput(input)), isPost, "게시글을 수정하지 못했어요.");
  await refreshCommunityPosts();
  return post;
}

export async function deleteCommunityPost(id: string) {
  await requestVoid(`/api/v1/community/posts/${pathId(id, "게시글")}/`, jsonInit("DELETE"), "게시글을 삭제하지 못했어요.");
  await refreshCommunityPosts();
}

export function fetchMyCommunityPosts() {
  return requestJson("/api/v1/community/posts/?mine=1", { cache: "no-store", signal: timeoutSignal() }, (value): value is TeamCommunityPost[] => Array.isArray(value) && value.every(isPost), "내 게시글을 불러오지 못했어요.");
}

export function fetchCommunityComments(postId: string, order: "oldest" | "newest" = "oldest") {
  if (order !== "oldest" && order !== "newest") throw new Error("댓글 정렬이 올바르지 않아요.");
  return requestJson(`/api/v1/community/posts/${pathId(postId, "게시글")}/comments/?order=${order}`, { cache: "no-store", signal: timeoutSignal() }, (value): value is CommunityComment[] => Array.isArray(value) && value.every(isComment), "댓글을 불러오지 못했어요.", false);
}

export async function createCommunityComment(postId: string, value: string) {
  const comment = await requestJson(`/api/v1/community/posts/${pathId(postId, "게시글")}/comments/`, jsonInit("POST", { content: content(value, "댓글", 2000) }), isComment, "댓글을 등록하지 못했어요.");
  await refreshCommunityPosts();
  return comment;
}

export async function updateCommunityComment(commentId: number, value: string) {
  const comment = await requestJson(`/api/v1/community/comments/${numericId(commentId, "댓글")}/`, jsonInit("PATCH", { content: content(value, "댓글", 2000) }), isComment, "댓글을 수정하지 못했어요.");
  await refreshCommunityPosts();
  return comment;
}

export async function deleteCommunityComment(commentId: number) {
  await requestVoid(`/api/v1/community/comments/${numericId(commentId, "댓글")}/`, jsonInit("DELETE"), "댓글을 삭제하지 못했어요.");
  await refreshCommunityPosts();
}

export function fetchCommunityVote(postId: string) {
  return requestJson(`/api/v1/community/posts/${pathId(postId, "게시글")}/vote/`, { cache: "no-store", signal: timeoutSignal() }, isVoteState, "추천 정보를 불러오지 못했어요.");
}

export async function setCommunityVote(postId: string, vote: "up" | "down" | null) {
  if (vote !== "up" && vote !== "down" && vote !== null) throw new Error("추천 값이 올바르지 않아요.");
  const result = await requestJson(`/api/v1/community/posts/${pathId(postId, "게시글")}/vote/`, jsonInit("POST", { vote }), isVoteState, "추천을 반영하지 못했어요.");
  await refreshCommunityPosts();
  return result;
}

export async function reportCommunityPost(postId: string, reason: CommunityReportReason, detail: string) {
  if (!reportReasons.has(reason)) throw new Error("신고 사유가 올바르지 않아요.");
  if (typeof detail !== "string") throw new Error("신고 상세가 올바르지 않아요.");
  const trimmedDetail = detail.trim();
  if (trimmedDetail.length > 50) throw new Error("신고 상세는 50자 이하로 입력해 주세요.");
  const report = await requestJson(`/api/v1/community/posts/${pathId(postId, "게시글")}/reports/`, jsonInit("POST", { reason, detail: trimmedDetail }), isReportResult, "게시글을 신고하지 못했어요.");
  await refreshCommunityPosts();
  return report;
}

const subscribe = (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); };
const serverState: CommunityState = { posts: [], loading: true, error: "" };

export function useCommunityPosts(enabled = true) {
  useEffect(() => { if (enabled) void retryCommunityPosts(); }, [enabled]);
  return useSyncExternalStore(subscribe, () => state, () => serverState);
}
