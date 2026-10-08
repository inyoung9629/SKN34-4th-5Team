import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GoogleLodgingPilot } from "@/components/google-lodging-pilot";

export const metadata: Metadata = {
  title: "Google 숙박 연동 로컬 테스트",
  robots: { index: false, follow: false },
  // Only this development page supplies an origin for referrer-restricted SDK keys.
  referrer: "strict-origin-when-cross-origin",
};

export default function GoogleLodgingPilotPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <GoogleLodgingPilot />;
}
