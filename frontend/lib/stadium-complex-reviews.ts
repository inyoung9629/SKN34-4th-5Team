import { stadiumComplexData } from "./stadium-complex-data";

type Coordinate = readonly [number, number];
export type ComplexReview = {
  checkedAt: string;
  status: "active_manual_trace";
  applyToSearch: true;
  outer: readonly Coordinate[];
  additionalOuters?: readonly (readonly Coordinate[])[];
  note: string;
};

/** The review map and search rules use exactly the same versioned geometry. */
export const stadiumComplexReviews: Readonly<Record<string, ComplexReview>> = stadiumComplexData.stadiums;

export function stadiumComplexReviewRings(code: string, mainStadiumFrame: readonly Coordinate[]) {
  const review = Object.hasOwn(stadiumComplexReviews, code) ? stadiumComplexReviews[code] : undefined;
  return review ? { outer: review.outer, outers: [review.outer, ...(review.additionalOuters ?? [])], hole: mainStadiumFrame } : null;
}
