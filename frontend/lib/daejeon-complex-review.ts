/**
 * Review-only trace of the light-blue sports-complex area on the displayed
 * Kakao basemap. This is NOT an official boundary supplied by Kakao and MUST
 * NOT be used for candidate filtering until the user has reviewed it.
 * The existing main-stadium frame is retained as the inner boundary, unchanged.
 * Display outlines only; do not replace the native blue fill or inflate this
 * outline into a bounding rectangle (which would include surrounding shops).
 */
export const daejeonComplexReview = {
  stadiumCode: "DAEJEON",
  status: "draft_visual_review",
  applyToSearch: false,
  checkedAt: "2026-10-02",
  outer: [
    [36.3175240, 127.4275779],
    [36.3178654, 127.4284584],
    [36.3183451, 127.4296550],
    [36.3190000, 127.4311226],
    [36.3190093, 127.4312016],
    [36.3189908, 127.4312807],
    [36.3189355, 127.4313484],
    [36.3184004, 127.4316532],
    [36.3178469, 127.4319919],
    [36.3173949, 127.4322515],
    [36.3169982, 127.4324886],
    [36.3163709, 127.4328950],
    [36.3156514, 127.4333353],
    [36.3154607, 127.4333625],
    [36.3154238, 127.4333286],
    [36.3148518, 127.4317482],
    [36.3149441, 127.4316918],
    [36.3149256, 127.4316353],
    [36.3148241, 127.4316805],
    [36.3140861, 127.4297388],
    [36.3140861, 127.4296824],
    [36.3141692, 127.4296147],
    [36.3145935, 127.4293324],
    [36.3152024, 127.4289373],
    [36.3158573, 127.4285197],
    [36.3165031, 127.4281020],
    [36.3170381, 127.4277633],
    [36.3175240, 127.4275779],
  ],
} as const;

type Coordinate = readonly [number, number];

export function complexReviewRings(code: string, mainStadiumFrame: readonly Coordinate[]) {
  if (code !== daejeonComplexReview.stadiumCode) return null;
  return { outer: daejeonComplexReview.outer, hole: mainStadiumFrame };
}
