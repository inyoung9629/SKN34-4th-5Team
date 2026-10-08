import { apiRequest } from "./client";
import type { Page } from "./types";
import { memberFetch } from "../member-auth-request";

// 운영 관리자용 게시글·신고 관리 API (backend/community/admin_views.py).
// 스키마(schema.d.ts)를 다시 생성하기 전까지는 백엔드 직렬화 필드를 그대로 옮긴 타입을 쓴다.
export type AdminPost = {
  source_id: string;
  post_number: string;
  board: "free" | "teams";
  team_code: string;
  category: string;
  title: string;
  author: string;
  created_at: string | null;
  views: number;
  comment_count: number;
  is_sample: boolean;
  is_hidden: boolean;
  report_count: number;
};

export type AdminPostOwner = {
  id: number;
  username: string;
  nickname: string;
  is_staff: boolean;
  is_superuser: boolean;
};

export type AdminReportReason = "spam" | "abuse" | "inappropriate" | "privacy" | "other";
export type AdminReportStatus = "pending" | "held" | "hidden";
export type AdminReport = {
  id: number;
  post: Pick<AdminPost, "source_id" | "post_number" | "board" | "team_code" | "title" | "author" | "is_hidden"> & { owner: AdminPostOwner | null };
  reporter: string;
  reason: AdminReportReason;
  detail: string;
  created_at: string;
  status: AdminReportStatus;
  handled_at: string | null;
};

/** 신고 처리: 보류 / 보류 취소 / 숨김 / 삭제(작성자 처분과 함께) */
export type AdminReportAction = "hold" | "unhold" | "hide" | "delete";
/** 삭제 팝업에서 고르는 처분 (현재는 기록용이며 계정에는 적용되지 않는다) */
export type AdminSanction = "none" | "7d" | "30d" | "permanent";
export type AdminReportActionResult = {
  action: AdminReportAction;
  sanction: AdminSanction;
  status: AdminReportStatus | null;
};

export const reportStatusLabel: Record<AdminReportStatus, string> = { pending: "처리 대기", held: "보류", hidden: "숨김" };
export const sanctionLabel: Record<AdminSanction, string> = { none: "보류", "7d": "7일 정지", "30d": "30일 정지", permanent: "영구 정지" };

export const reportReasonLabel: Record<AdminReportReason, string> = {
  spam: "스팸·광고", abuse: "욕설·비방", inappropriate: "부적절한 내용", privacy: "개인정보 노출", other: "기타",
};

export const listAdminPosts = (query: URLSearchParams, signal?: AbortSignal) => apiRequest<Page<AdminPost>>(`/api/v1/community/admin/posts/?${query}`, {
  cache: "no-store", signal,
}, memberFetch);

export const deleteAdminPost = (sourceId: string) => apiRequest<never>(`/api/v1/community/admin/posts/${encodeURIComponent(sourceId)}/`, {
  method: "DELETE",
}, memberFetch);

export const listAdminReports = (query: URLSearchParams, signal?: AbortSignal) => apiRequest<Page<AdminReport>>(`/api/v1/community/admin/reports/?${query}`, {
  cache: "no-store", signal,
}, memberFetch);

export const actOnAdminReport = (id: number, action: AdminReportAction, sanction: AdminSanction = "none") => apiRequest<AdminReportActionResult>(`/api/v1/community/admin/reports/${id}/action/`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, sanction }),
}, memberFetch);
