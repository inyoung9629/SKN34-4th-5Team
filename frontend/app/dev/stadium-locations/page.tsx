import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { StadiumLocationReview } from "@/components/stadium-location-review";
import { stadiumLocationAudit } from "@/lib/stadium-locations";

export const metadata: Metadata = { title: "1군 구장 위치 확인", robots: { index: false, follow: false } };
export default async function Page({ searchParams }: { searchParams: Promise<{ stadium?: string | string[] }> }) {
  if (process.env.NODE_ENV !== "development") notFound();
  const { stadium } = await searchParams;
  const initialCode = typeof stadium === "string" && Object.hasOwn(stadiumLocationAudit.stadiums, stadium)
    ? stadium as keyof typeof stadiumLocationAudit.stadiums : "JAMSIL";
  return <StadiumLocationReview initialCode={initialCode} />;
}
