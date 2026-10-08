import type { components, operations } from "./schema";
import { apiRequest } from "./client";
import type { Page } from "./types";
import { memberFetch } from "../member-auth-request";

export type MemberUser = components["schemas"]["MemberUser"];
export type MemberUserUpdate = components["schemas"]["PatchedMemberUserUpdate"];
export type AdminMember = components["schemas"]["AdminMember"];
export type AdminRoleUpdate = NonNullable<operations["v1_auth_admin_members_role_partial_update"]["requestBody"]>["content"]["application/json"];
export type SignInRequest = components["schemas"]["SignInRequest"];
export type TokenPair = components["schemas"]["TokenPair"];
export type SignupRequest = components["schemas"]["Signup"];
export type EmailRequest = components["schemas"]["SendEmail"];
export type PasswordUpdateRequest = components["schemas"]["PasswordUpdateRequest"];
export type EmailVerificationRequest = components["schemas"]["EmailVerificationRequest"];

export const signIn = (body: SignInRequest, signal?: AbortSignal) => apiRequest<TokenPair>("/api/v1/auth/signin", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
});

export const signUp = (body: SignupRequest, signal?: AbortSignal) => apiRequest<never>("/api/v1/auth/signup/", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
});

export const requestUsername = (body: EmailRequest, signal?: AbortSignal) => apiRequest<components["schemas"]["UsernameRequestResponse"]>("/api/v1/auth/username/request", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
});

export const requestPasswordReset = (body: EmailRequest, signal?: AbortSignal) => apiRequest<never>("/api/v1/auth/password/request", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
});

export const updatePassword = (body: PasswordUpdateRequest, authenticated = false, signal?: AbortSignal) => apiRequest<never>("/api/v1/auth/password", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
}, authenticated ? memberFetch : fetch);

export const getMemberUser = (signal?: AbortSignal) => apiRequest<MemberUser>("/api/v1/auth/user", {
  cache: "no-store", signal,
}, memberFetch);

export const updateMemberUser = (body: MemberUserUpdate, signal?: AbortSignal) => apiRequest<MemberUser>("/api/v1/auth/user", {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
}, memberFetch);

export const requestEmailChange = (body: EmailRequest, signal?: AbortSignal) => apiRequest<components["schemas"]["EmailChangeRequestResponse"]>("/api/v1/auth/email/request", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
}, memberFetch);

export const verifyEmailChange = (body: EmailVerificationRequest, signal?: AbortSignal) => apiRequest<components["schemas"]["EmailVerificationResponse"]>("/api/v1/auth/email/verify", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
}, memberFetch);

export const listAdminMembers = (query: URLSearchParams, signal?: AbortSignal) => apiRequest<Page<AdminMember>>(`/api/v1/auth/admin/members/?${query}`, {
  cache: "no-store", signal,
}, memberFetch);

export const updateAdminRole = (id: number, body: AdminRoleUpdate) => apiRequest<AdminMember>(`/api/v1/auth/admin/members/${id}/role/`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}, memberFetch);
