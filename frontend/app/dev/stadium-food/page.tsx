import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { StadiumFacilityReview } from "@/components/stadium-facility-review";

export const metadata: Metadata = { title: "구장 먹거리·시설 위치 검토", robots: { index: false, follow: false } };
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <StadiumFacilityReview />;
}
