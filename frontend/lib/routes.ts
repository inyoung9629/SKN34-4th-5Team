"use client";

import { useEffect, useSyncExternalStore } from "react";
import { fetchCourseReaction, fetchCourses, persistCourse, recordCourseView, removeCourse, setCourseReaction } from "./course-api";
import { createClientId } from "./client-id";
import { isLodgingReference } from "./google-lodging";
import { isRichContentDoc, type RichContentDoc } from "./community-rich-content";
import type { LegModes, TravelMode } from "./course-directions";
import type { ChatCoursePlace, ChatCourse } from "./chat/types";

export type RouteStop = { name: string; lat: number; lng: number; category: string; placeId?: string; visitId?: string; address?: string; tourContentId?: string; isMapPoint?: boolean; isDrawnPoint?: boolean; coursePlace?: ChatCoursePlace; courseGame?: ChatCourse["game"]; courseProgress?: ChatCourse["progress"] };
export function areValidCoordinates(lat: unknown, lng: unknown): boolean {
  return typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
}
export type TripRoute = {
  id: string; title: string; stadium: string; description: string; content: string;
  tags: string[]; duration: string; cover: string; stops: RouteStop[];
  author: string; likes: number; isSample: boolean; createdAt: string;
  views?: number; contentFormat?: "html"; contentDoc?: RichContentDoc | null;
  routeNumber?: string;
  apiId?: string;
  start?: { lat: number; lng: number; name?: string };
  travelMode?: TravelMode;
  legModes?: LegModes;
  owned?: boolean;
  legacy?: boolean;
  legacySourceId?: string;
  saveWarning?: string;
};

const VIEW_TOKEN_KEY = "kbo-course-view-token";
const ROUTES_KEY = "kbo-trip-routes-v1";
const viewedThisSession = new Set<string>();
const CHANGE_EVENT = "kbo-routes-change";
const EMPTY = "[]";
const EMPTY_ROUTES: TripRoute[] = [];

function readStorage(key: string): string {
  if (typeof window === "undefined") return EMPTY;
  try { return window.localStorage.getItem(key) || EMPTY; } catch { return EMPTY; }
}

function isRoute(value: unknown): value is TripRoute {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return ["id", "title", "stadium", "description", "content", "duration", "cover", "author", "createdAt"].every(key => typeof item[key] === "string")
    && typeof item.likes === "number" && Number.isFinite(item.likes) && typeof item.isSample === "boolean"
    && (item.views === undefined || (typeof item.views === "number" && Number.isFinite(item.views) && item.views >= 0))
    && (item.contentFormat === undefined || item.contentFormat === "html")
    && (item.contentDoc === undefined || item.contentDoc === null || isRichContentDoc(item.contentDoc))
    && (item.owned === undefined || typeof item.owned === "boolean")
    && (item.legacy === undefined || typeof item.legacy === "boolean")
    && (item.legacySourceId === undefined || typeof item.legacySourceId === "string")
    && (item.saveWarning === undefined || typeof item.saveWarning === "string")
    && (item.apiId === undefined || typeof item.apiId === "string")
    && (item.start === undefined || (item.start !== null && typeof item.start === "object" && areValidCoordinates((item.start as Record<string, unknown>).lat, (item.start as Record<string, unknown>).lng)))
    && Array.isArray(item.tags) && item.tags.every(tag => typeof tag === "string")
    && Array.isArray(item.stops) && item.stops.every(stop => stop && typeof stop === "object" && typeof stop.name === "string" && typeof stop.category === "string" && (stop.placeId === undefined || typeof stop.placeId === "string") && (stop.visitId === undefined || typeof stop.visitId === "string") && (stop.address === undefined || typeof stop.address === "string") && (stop.tourContentId === undefined || typeof stop.tourContentId === "string") && (stop.isMapPoint === undefined || typeof stop.isMapPoint === "boolean") && (stop.isDrawnPoint === undefined || typeof stop.isDrawnPoint === "boolean") && (isLodgingReference(stop) || areValidCoordinates(stop.lat, stop.lng)));
}

function parseStoredRoutes(raw: string): TripRoute[] {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRoute).filter(route => !route.isSample).map(route => ({ ...route, owned: true, legacy: true }));
  } catch { return []; }
}

function removeStoredRoute(id: string): void {
  const parsed: unknown = JSON.parse(readStorage(ROUTES_KEY));
  if (!Array.isArray(parsed)) throw new Error("저장된 코스 정보를 확인해 주세요.");
  persist(ROUTES_KEY, parsed.filter(item => !item || typeof item !== "object" || (item as Record<string, unknown>).id !== id));
}

function subscribeLikes(callback: () => void) {
  window.addEventListener(CHANGE_EVENT, callback);
  return () => window.removeEventListener(CHANGE_EVENT, callback);
}

function persist(key: string, value: unknown): void {
  if (typeof window === "undefined") throw new Error("브라우저에서 다시 시도해 주세요.");
  try { window.localStorage.setItem(key, JSON.stringify(value)); }
  catch { throw new Error("브라우저 저장 공간을 사용할 수 없어요. 저장 권한과 남은 공간을 확인해 주세요."); }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function getRoutes(): TripRoute[] {
  return routeSnapshot;
}

const routeListeners = new Set<() => void>();
const migratedLegacyIds = new Set<string>();
let serverRoutes: TripRoute[] = [];
let routeSnapshot = EMPTY_ROUTES;
let routesReady = false;
let routesError = "";
let refreshing: Promise<void> | undefined;
const legacyRoutes = () => parseStoredRoutes(readStorage(ROUTES_KEY)).filter(route => !migratedLegacyIds.has(route.id));
const publishRoutes = () => { routeSnapshot = [...serverRoutes, ...legacyRoutes()]; routeListeners.forEach(listener => listener()); };
const subscribeRoutes = (listener: () => void) => {
  routeListeners.add(listener);
  window.addEventListener("storage", publishRoutes);
  return () => { routeListeners.delete(listener); window.removeEventListener("storage", publishRoutes); };
};
function refreshRoutes() {
  if (!refreshing) {
    routesError = "";
    refreshing = fetchCourses().then(routes => { serverRoutes = routes; }).catch(() => { routesError = "코스 목록을 불러오지 못했어요."; }).finally(() => { routesReady = true; publishRoutes(); refreshing = undefined; });
    publishRoutes();
  }
  return refreshing;
}

export const retryRoutes = () => refreshRoutes();

export async function saveRoute(route: TripRoute): Promise<TripRoute> {
  if (!isRoute(route) || route.isSample) throw new Error("저장할 코스 정보를 다시 확인해 주세요.");
  const saved = await persistCourse(route);
  const published = route.legacy ? { ...saved, legacySourceId: route.id } : route.legacySourceId ? { ...saved, legacySourceId: route.legacySourceId } : saved;
  if (route.legacy) {
    migratedLegacyIds.add(route.id);
    try { removeStoredRoute(route.id); } catch {}
  }
  serverRoutes = [published, ...serverRoutes.filter(item => item.id !== published.id)];
  publishRoutes();
  return published;
}

export async function deleteRoute(id: string): Promise<void> {
  if (legacyRoutes().some(route => route.id === id)) {
    removeStoredRoute(id);
    migratedLegacyIds.add(id);
    publishRoutes();
    return;
  }
  await removeCourse(id);
  serverRoutes = serverRoutes.filter(route => route.id !== id);
  publishRoutes();
}

export function useRoutes(): TripRoute[] {
  useEffect(() => { void refreshRoutes(); }, []);
  return useSyncExternalStore(subscribeRoutes, () => routeSnapshot, () => EMPTY_ROUTES);
}

export function useRoutesReady(): boolean {
  useEffect(() => { void refreshRoutes(); }, []);
  return useSyncExternalStore(subscribeRoutes, () => routesReady, () => false);
}

export function useRoutesError(): string {
  useEffect(() => { void refreshRoutes(); }, []);
  return useSyncExternalStore(subscribeRoutes, () => routesError, () => "");
}

let sessionViewToken = "";
function viewToken(): string {
  if (sessionViewToken) return sessionViewToken;
  try {
    const stored = window.localStorage.getItem(VIEW_TOKEN_KEY) || "";
    sessionViewToken = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(stored) ? stored : createClientId();
    window.localStorage.setItem(VIEW_TOKEN_KEY, sessionViewToken);
  } catch { sessionViewToken = createClientId(); }
  return sessionViewToken;
}

function updateRoute(id: string, changes: Partial<TripRoute>) {
  serverRoutes = serverRoutes.map(route => route.id === id ? { ...route, ...changes } : route);
  publishRoutes();
}

export function recordRouteView(id: string): void {
  if (viewedThisSession.has(id)) return;
  const route = serverRoutes.find(item => item.id === id);
  if (!route) return;
  viewedThisSession.add(id);
  void recordCourseView(route.apiId ?? route.id, viewToken())
    .then(({ views }) => updateRoute(id, { views }))
    .catch(() => { /* Viewing a route must still work if metrics are unavailable. */ });
}

const likedRoutes = new Set<string>();
let likesRevision = 0;
let likeUserId: number | null = null;
let likeGeneration = 0;
const reactionQueues = new Map<string, Promise<unknown>>();
const reactionLoads = new Map<string, Promise<void>>();

function notifyLikes() {
  likesRevision += 1;
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function resetRouteLikes(userId: number | null) {
  if (likeUserId === userId) return;
  likeUserId = userId;
  likeGeneration += 1;
  likedRoutes.clear();
  reactionQueues.clear();
  reactionLoads.clear();
  notifyLikes();
}

function assertLikeSession(generation: number) {
  if (likeUserId === null || generation !== likeGeneration) {
    throw new Error("로그인 상태가 바뀌었어요. 다시 시도해 주세요.");
  }
}

function reactionTarget(id: string) {
  const route = serverRoutes.find(item => item.id === id);
  if (!route) throw new Error("서버에 저장된 코스만 좋아요를 남길 수 있어요.");
  return route.apiId ?? route.id;
}

function applyReaction(id: string, result: { liked: boolean; likes: number }, generation: number) {
  assertLikeSession(generation);
  if (result.liked) likedRoutes.add(id); else likedRoutes.delete(id);
  updateRoute(id, { likes: result.likes });
  notifyLikes();
}

function enqueueReaction<T>(id: string, operation: () => Promise<T>): Promise<T> {
  const previous = reactionQueues.get(id) ?? Promise.resolve();
  const request = previous.catch(() => undefined).then(operation);
  reactionQueues.set(id, request);
  const cleanup = () => {
    if (reactionQueues.get(id) === request) reactionQueues.delete(id);
  };
  void request.then(cleanup, cleanup);
  return request;
}

export function useLikedRoutes(): string[] {
  useSyncExternalStore(subscribeLikes, () => likesRevision, () => 0);
  return [...likedRoutes];
}

export function loadRouteLike(id: string): Promise<void> {
  const existing = reactionLoads.get(id);
  if (existing) return existing;
  const generation = likeGeneration;
  const request = enqueueReaction(id, async () => {
    assertLikeSession(generation);
    const result = await fetchCourseReaction(reactionTarget(id));
    applyReaction(id, result, generation);
  });
  reactionLoads.set(id, request);
  const cleanup = () => {
    if (reactionLoads.get(id) === request) reactionLoads.delete(id);
  };
  void request.then(cleanup, cleanup);
  return request;
}

export function toggleRouteLike(id: string): Promise<boolean> {
  return mutateRouteLike(id);
}

export function setRouteLike(id: string, liked: boolean): Promise<boolean> {
  return mutateRouteLike(id, liked);
}

function mutateRouteLike(id: string, liked?: boolean): Promise<boolean> {
  const generation = likeGeneration;
  return enqueueReaction(id, async () => {
    assertLikeSession(generation);
    const apiId = reactionTarget(id);
    const desired = liked ?? !(await fetchCourseReaction(apiId)).liked;
    assertLikeSession(generation);
    const result = await setCourseReaction(apiId, desired);
    applyReaction(id, result, generation);
    return result.liked;
  });
}
