import type { TripRoute } from "./routes";
import type { CourseCreateRequestDto, CourseCreateResultDto, CourseDto, CoursePatchRequestDto, CourseReactionDto, CourseViewResultDto } from "./api/content";
import { ApiError, apiRequest } from "./api/client";
import { memberFetch } from "./member-auth-request";
import { referenceOnlyStop } from "./google-lodging";

const TOKEN_PREFIX = "kbo-course-edit-token:";
const sessionTokens = new Map<string, string>();
const TOKEN_WARNING = "코스는 공개 저장됐지만 편집 권한을 브라우저에 저장하지 못했어요. 이 페이지를 새로고침하면 읽기 전용이 됩니다.";

function token(id: string) {
  if (sessionTokens.has(id)) return sessionTokens.get(id)!;
  try { return window.localStorage.getItem(`${TOKEN_PREFIX}${id}`) || ""; } catch { return ""; }
}

function rememberToken(id: string, value: string) {
  sessionTokens.set(id, value);
  try { window.localStorage.setItem(`${TOKEN_PREFIX}${id}`, value); return ""; }
  catch { return TOKEN_WARNING; }
}

function routeFromApi(value: CourseDto, owned = Boolean(token(value.id)), saveWarning = ""): TripRoute {
  if (!value || typeof value.id !== "string" || !Array.isArray(value.stops)) throw new Error("코스 서버 응답을 확인해 주세요.");
  const stops = [...value.stops].sort((a, b) => a.position - b.position).map(stop => { const copy = { ...stop, lat: stop.lat ?? NaN, lng: stop.lng ?? NaN }; Reflect.deleteProperty(copy, "position"); return referenceOnlyStop(copy); });
  const description = value.description || (value.content?.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() || stops.map(stop => stop.name).join(" → ")).slice(0, 100);
  return {
    id: value.sampleId ?? value.id, ...(value.sampleId ? { apiId: value.id } : {}), routeNumber: value.routeNumber, title: value.title, stadium: value.stadium, description, content: value.content ?? "",
    ...(value.contentFormat ? { contentFormat: value.contentFormat } : {}), ...(value.contentDoc ? { contentDoc: value.contentDoc } : {}), tags: value.tags, duration: value.duration,
    cover: value.cover || "/images/stadium-night.jpg", stops, ...(value.startLat !== undefined && value.startLng !== undefined ? { start: { lat: value.startLat, lng: value.startLng } } : {}),
    author: value.author, likes: value.likes ?? 0, views: value.views ?? 0, isSample: value.isSample ?? false, owned: value.isSample ? false : owned, createdAt: value.createdAt, ...(saveWarning ? { saveWarning } : {}),
  };
}

function payload(route: TripRoute, editing: boolean): CourseCreateRequestDto | CoursePatchRequestDto {
  return {
    title: route.title, stadium: route.stadium, content: route.content, contentDoc: route.contentDoc ?? null, contentFormat: route.contentFormat ?? "",
    duration: route.duration, tags: route.tags, ...(route.start ? { startLat: route.start.lat, startLng: route.start.lng } : editing ? { startLat: null, startLng: null } : {}),
    stops: route.stops.map((stop, position) => ({ ...referenceOnlyStop(stop), position })),
  };
}

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

async function courseRequest<T>(fetcher: Fetcher, url: string, init: RequestInit = {}): Promise<T | null> {
  try { return await apiRequest<T>(url, { ...init, redirect: "error", signal: AbortSignal.timeout(40000) }, fetcher); }
  catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error("코스 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
}

export async function fetchCourses(fetcher: Fetcher = fetch): Promise<TripRoute[]> {
  const value = await courseRequest<unknown>(fetcher, "/api/v1/courses/", { cache: "no-store" });
  if (!Array.isArray(value)) throw new Error("코스 서버 응답을 확인해 주세요.");
  return value.map(course => routeFromApi(course));
}

export async function persistCourse(route: TripRoute, fetcher: Fetcher = fetch): Promise<TripRoute> {
  const editToken = route.id ? token(route.id) : "";
  if (route.id && route.owned && !route.legacy && !editToken) throw new Error("이 코스의 편집 토큰을 찾을 수 없어 읽기만 가능해요.");
  const response = await courseRequest<CourseDto | CourseCreateResultDto>(fetcher === fetch ? memberFetch : fetcher, editToken ? `/api/v1/courses/${encodeURIComponent(route.id)}/` : "/api/v1/courses/", {
    method: editToken ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json", ...(editToken ? { "X-Course-Edit-Token": editToken } : {}) },
    body: JSON.stringify(payload(route, Boolean(editToken))),
  });
  if (!response) throw new Error("코스 서버 응답을 확인해 주세요.");
  let saveWarning = "";
  if (!editToken) {
    if (!("editToken" in response) || typeof response.editToken !== "string" || !response.editToken) throw new Error("코스 편집 토큰을 받지 못했어요.");
    saveWarning = rememberToken(response.id, response.editToken);
  } else saveWarning = rememberToken(response.id, editToken);
  return routeFromApi(response, true, saveWarning);
}

export async function fetchCourseReaction(id: string): Promise<CourseReactionDto> {
  const result = await courseRequest<CourseReactionDto>(memberFetch, `/api/v1/courses/${encodeURIComponent(id)}/reaction/`, { cache: "no-store" });
  if (!result) throw new Error("좋아요 응답을 확인해 주세요.");
  return result;
}

export async function setCourseReaction(id: string, liked: boolean): Promise<CourseReactionDto> {
  const result = await courseRequest<CourseReactionDto>(memberFetch, `/api/v1/courses/${encodeURIComponent(id)}/reaction/`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ liked }),
  });
  if (!result) throw new Error("좋아요 응답을 확인해 주세요.");
  return result;
}

export async function recordCourseView(id: string, viewToken: string, fetcher: Fetcher = fetch): Promise<CourseViewResultDto> {
  const result = await courseRequest<CourseViewResultDto>(fetcher, `/api/v1/courses/${encodeURIComponent(id)}/view/`, {
    method: "POST", headers: { "X-Course-View-Token": viewToken },
  });
  if (!result) throw new Error("조회수 응답을 확인해 주세요.");
  return result;
}

export async function removeCourse(id: string, fetcher: Fetcher = fetch): Promise<void> {
  const editToken = token(id);
  if (!editToken) throw new Error("이 코스의 편집 토큰을 찾을 수 없어 삭제할 수 없어요.");
  await courseRequest(fetcher === fetch ? memberFetch : fetcher, `/api/v1/courses/${encodeURIComponent(id)}/`, { method: "DELETE", headers: { "X-Course-Edit-Token": editToken } });
  sessionTokens.delete(id);
  try { window.localStorage.removeItem(`${TOKEN_PREFIX}${id}`); } catch {}
}
