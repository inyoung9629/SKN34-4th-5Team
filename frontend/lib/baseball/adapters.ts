import { stadiums as presentation } from "@/lib/stadiums";
import { safeChatUrl } from "@/lib/media-url";
import type { Stadium } from "@/lib/stadiums";
import type { BaseballStadium } from "./types";

export function adaptStadium(row: BaseballStadium): Stadium | null {
  const visual = presentation.find((item) => item.code === row.stadium_code);
  const lng = Number(row.longitude), lat = Number(row.latitude);
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || Math.abs(lng) > 180 || Math.abs(lat) > 90) return null;
  return {
    code: row.stadium_code, name: row.stadium_name_ko, address: row.address,
    lng, lat, region: visual?.region ?? "미분류",
    teams: [...new Set(row.home_teams.map((team) => team.name))], color: visual?.color ?? "blue",
    seatingMap: row.seatingMap && safeChatUrl(row.seatingMap.imageUrl, true) ? {
      src: row.seatingMap.imageUrl, sourceUrl: safeChatUrl(row.seatingMap.sourceUrl ?? "") ?? "",
      season: row.seatingMap.season, teamCode: row.seatingMap.team_code,
    } : undefined,
    parkingMap: row.parkingMap && safeChatUrl(row.parkingMap.imageUrl, true) && row.parkingMap.width && row.parkingMap.height && row.parkingMap.kind ? {
      stadiumCode: row.stadium_code, stadiumName: row.stadium_name_ko,
      src: row.parkingMap.imageUrl, width: row.parkingMap.width, height: row.parkingMap.height,
      kind: row.parkingMap.kind, title: row.parkingMap.title ?? "", summary: row.parkingMap.summary ?? "",
      visualNotes: row.parkingMap.visualNotes ?? [], capturedAt: row.parkingMap.capturedAt ?? "",
      sourcePageUrl: safeChatUrl(row.parkingMap.sourceUrl ?? "") ?? "", credit: row.parkingMap.credit ?? "",
    } : undefined,
    cardImage: row.image_url && safeChatUrl(row.image_url, true) ? {
      src: row.image_url, sourceUrl: safeChatUrl(row.image_source_url ?? "") ?? "",
      credit: row.image_credit ?? "", creditUrl: safeChatUrl(row.image_credit_url ?? "") ?? "",
      licenseUrl: safeChatUrl(row.image_license_url ?? ""), objectPosition: visual?.photoPosition,
    } : undefined,
  };
}
