import type { Metadata } from "next";
import type { ReactNode } from "react";

// Google UI Kit's requests require the origin for the website-restricted key.
// Kakao SDK retains its own explicit no-referrer policy.
export const metadata: Metadata = { referrer: "strict-origin-when-cross-origin" };
export default function RoutesLayout({ children }: { children: ReactNode }) { return children; }
