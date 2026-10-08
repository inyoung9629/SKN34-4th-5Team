import { apiRequest, ApiError } from "../api/client";
import type { Page } from "../api/types";
import { memberFetch } from "../member-auth-request";
import type { AdminDetailDtoMap, AdminResourceDtoMap, AdminResourceName } from "./wire";
import type { BaseballStadium, TicketPolicy } from "./types";

export class BaseballApiError extends Error {
  constructor(message: string, readonly status = 0) { super(message); }
}

async function baseballRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/v1/baseball/${path}`, { cache: "no-store", signal }); }
  catch { throw new BaseballApiError("야구 정보 서버에 연결하지 못했어요."); }
  if (!response.ok) throw new BaseballApiError(response.status === 404 ? "구장 정보를 찾을 수 없어요." : "야구 정보를 불러오지 못했어요.", response.status);
  return response.json() as Promise<T>;
}

export async function fetchBaseballStadiums(signal?: AbortSignal) {
  let pageNumber = 1;
  let page = await baseballRequest<Page<BaseballStadium>>(`stadiums/?page=${pageNumber}&page_size=100`, signal);
  const results = [...page.results];
  while (results.length < page.count && page.results.length) {
    page = await baseballRequest<Page<BaseballStadium>>(`stadiums/?page=${++pageNumber}&page_size=100`, signal);
    results.push(...page.results);
  }
  if (results.length < page.count) throw new BaseballApiError("구장 목록을 끝까지 불러오지 못했어요.");
  return { ...page, count: results.length, next: null, previous: null, results };
}
export const fetchBaseballStadium = (code: string, signal?: AbortSignal) => baseballRequest<BaseballStadium>(`stadiums/${encodeURIComponent(code)}/`, signal);
export const fetchStadiumSection = <T>(code: string, section: string, page = 1, homeContext?: number, signal?: AbortSignal) => baseballRequest<Page<T>>(`stadiums/${encodeURIComponent(code)}/${section}/?${new URLSearchParams({ page: String(page), page_size: "30", ...(homeContext ? { home_context: String(homeContext) } : {}) })}`, signal);
export const fetchTicketPolicies = (team: number, page = 1, signal?: AbortSignal) => baseballRequest<Page<TicketPolicy>>(`ticket-policies/?${new URLSearchParams({ team: String(team), page: String(page), page_size: "30" })}`, signal);

const adminPath = (name: AdminResourceName, id?: number) =>
  `/api/v1/baseball/manage/${name}/${id === undefined ? "" : `${id}/`}`;

export async function fetchAdminPage<Name extends AdminResourceName>(
  name: Name,
  page = 1,
  pageSize = 30,
  query = "",
  signal?: AbortSignal,
): Promise<Page<AdminResourceDtoMap[Name]>> {
  const value = await apiRequest<Page<AdminResourceDtoMap[Name]>>(
    `${adminPath(name)}?${new URLSearchParams({ page: String(page), page_size: String(pageSize), q: query })}`,
    { cache: "no-store", signal },
    memberFetch,
  );
  if (value === null) throw new ApiError("목록 응답이 비어 있어요.", 502);
  if (!Array.isArray(value.results) || typeof value.count !== "number") throw new ApiError("목록 응답 형식이 올바르지 않아요.", 502);
  return value;
}

export async function fetchAdminDetail<Name extends AdminResourceName>(name: Name, id: number, signal?: AbortSignal) {
  const value = await apiRequest<AdminDetailDtoMap[Name]>(adminPath(name, id), { cache: "no-store", signal }, memberFetch);
  if (value === null) throw new ApiError("상세 응답이 비어 있어요.", 502);
  if (typeof value.id !== "number" || typeof value._etag !== "string") throw new ApiError("상세 응답 형식이 올바르지 않아요.", 502);
  return value;
}

export async function createAdminRow<Name extends AdminResourceName>(name: Name, values: Partial<AdminResourceDtoMap[Name]>) {
  const value = await apiRequest<AdminResourceDtoMap[Name]>(adminPath(name), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values),
  }, memberFetch);
  if (value === null) throw new ApiError("생성 응답이 비어 있어요.", 502);
  if (typeof value.id !== "number") throw new ApiError("생성 응답 형식이 올바르지 않아요.", 502);
  return value;
}

export async function updateAdminRow<Name extends AdminResourceName>(name: Name, id: number, etag: string, values: Partial<AdminResourceDtoMap[Name]>) {
  const value = await apiRequest<AdminDetailDtoMap[Name]>(adminPath(name, id), {
    method: "PATCH", headers: { "Content-Type": "application/json", "If-Match": etag }, body: JSON.stringify(values),
  }, memberFetch);
  if (value === null) throw new ApiError("수정 응답이 비어 있어요.", 502);
  return value;
}

export async function deleteAdminRow(name: AdminResourceName, id: number, etag: string) {
  const value = await apiRequest<never>(adminPath(name, id), { method: "DELETE", headers: { "If-Match": etag } }, memberFetch);
  if (value !== null) throw new ApiError("삭제 응답 형식이 올바르지 않아요.", 502);
}
