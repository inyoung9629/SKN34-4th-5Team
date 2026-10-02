import type { components } from "./schema";
import type { RichContentDoc } from "../community-rich-content";

type Schemas = components["schemas"];

export type CourseDto = Schemas["Course"] & { contentDoc?: RichContentDoc | null };
export type CourseCreateRequestDto = Schemas["CourseCreateRequest"] & { contentDoc?: RichContentDoc | null };
export type CoursePatchRequestDto = Schemas["PatchedCoursePatchRequest"] & { contentDoc?: RichContentDoc | null };
export type CourseCreateResultDto = Schemas["CourseCreateResult"] & { contentDoc?: RichContentDoc | null };
export type CourseReactionRequestDto = Schemas["CourseReactionRequest"];
export type CourseReactionDto = Schemas["CourseReaction"];
export type CourseViewResultDto = Schemas["CourseViewResult"];
export type CommunityPostWriteDto = Schemas["CommunityPostWrite"] & { contentDoc?: RichContentDoc | null };
export type CommunityPostPatchDto = Schemas["PatchedCommunityPostPatch"] & { contentDoc?: RichContentDoc | null };
export type CommunityPostDto = Schemas["CommunityPost"] & { contentDoc?: RichContentDoc | null };
export type CommunityCommentWriteDto = Schemas["CommunityCommentWrite"];
export type CommunityCommentDto = Schemas["CommunityComment"];
export type CommunityVoteWriteDto = Schemas["CommunityVoteWrite"];
export type CommunityVoteStateDto = Schemas["CommunityVoteState"];
export type CommunityReportWriteDto = Schemas["CommunityReportWrite"];
export type CommunityReportResultDto = Schemas["CommunityReportResult"];
export type PredictionChoiceWriteDto = Schemas["PredictionChoiceWrite"];
export type PredictionTeamDto = Schemas["PredictionTeam"];
export type PredictionVotesDto = Schemas["PredictionVotes"];
export type PredictionGameDto = Schemas["PredictionGame"];

export type CommunityMemberSummaryDto = Schemas["PublicMember"];
export type CommunityMemberPostDto = Pick<Schemas["CommunityPost"], "id" | "title" | "board" | "teamCode" | "createdAt">;
export type CommunityMemberCommentDto = Schemas["CommunityMemberComment"];
export type CommunityMemberPageDto<T> = Omit<Schemas["CommunityPostPage"], "results"> & { results: T[] };
