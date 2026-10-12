import type { Metadata } from "next";
import { PredictionBoard } from "@/components/prediction-board";

export const metadata: Metadata = { title: "환상게임" };

export default function FantasyPage() {
  return <PredictionBoard />;
}
