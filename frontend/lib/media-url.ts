// Actual sources: TVING profiles/logos, facility catalogue photos, and public stadium assets.
const imageOrigins = new Set(["https://image.tving.com", "https://myseatcheck.com"]);
const localImages = new Set([
  "/images/stadiums/exteriors/jamsil.jpg", "/images/stadiums/exteriors/gocheok.jpg",
  "/images/stadiums/exteriors/munhak-exterior.jpg", "/images/stadiums/exteriors/suwon.jpg",
  "/images/stadiums/exteriors/daejeon.jpg", "/images/stadiums/exteriors/daegu.jpg",
  "/images/stadiums/exteriors/gwangju.png", "/images/stadiums/exteriors/sajik.gif",
  "/images/stadiums/exteriors/changwon.png",
  "/images/stadiums/seating-maps/jamsil.png", "/images/stadiums/seating-maps/gocheok.jpg",
  "/images/stadiums/seating-maps/munhak.png", "/images/stadiums/seating-maps/suwon.jpg",
  "/images/stadiums/seating-maps/daejeon.png", "/images/stadiums/seating-maps/daegu.jpg",
  "/images/stadiums/seating-maps/gwangju.png", "/images/stadiums/seating-maps/sajik.jpg",
  "/images/stadiums/seating-maps/changwon.jpg",
  ...["jamsil", "gocheok", "incheon", "suwon", "daegu", "gwangju", "sajik", "changwon"].map(name => `/images/stadiums/parking-maps/${name}-parking.png`),
  "/images/stadiums/parking-maps/daejeon-parking.jpg",
]);
const publicPath = /^(?:\/(?:chat|standings|schedule|stadiums|guide|highlights|routes|community)?|\/standings\/(?:players\/[0-9]+|teams\/[A-Z0-9]+)|\/stadiums\/[A-Z]+|\/routes\/(?!new(?:#|$))[A-Za-z0-9-]+|\/community\/(?:teams|predictions|members\/[0-9]+))$/;

export function safeChatUrl(value: string, image = false): string | undefined {
  if (!value || /[\s\\\u0000-\u001f\u007f]/.test(value) || value.startsWith("//")) return;
  try {
    if (value.startsWith("/")) {
      // Encoded separators/traversal are forbidden in paths, not opaque post query IDs.
      if (image) return localImages.has(value) ? value : undefined;
      const url = new URL(value, "https://chat.invalid");
      if (url.origin !== "https://chat.invalid" || !publicPath.test(url.pathname) || /%|\/\./.test(value.split(/[?#]/, 1)[0])) return;
      // Community source_id is at most 40 characters; encoded spaces/slashes are public IDs.
      const postId = /^(?!\/\/)[^\\:\u0000-\u001f\u007f]{1,40}$/;
      const queryRules: Record<string, Record<string, RegExp>> = {
        "/community": { post: postId },
        "/community/teams": { post: postId, team: /^[A-Z0-9]+$/ },
        "/community/predictions": { date: /^\d{4}-\d{2}-\d{2}$/, team: /^[A-Z0-9]+$/, game: /^[A-Za-z0-9_-]+$/ },
      };
      const rules = queryRules[url.pathname] ?? (/^\/community\/members\/[0-9]+$/.test(url.pathname) ? { tab: /^(posts|comments)$/, page: /^[1-9][0-9]*$/ } : {});
      const seen = new Set<string>();
      for (const [key, parameter] of url.searchParams) {
        if (seen.has(key) || !Object.hasOwn(rules, key) || !rules[key].test(parameter)) return;
        seen.add(key);
      }
      return value;
    }
    if (!/^https:\/\//i.test(value)) return;
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return;
    if (image && !imageOrigins.has(url.origin)) return;
    return url.href;
  } catch { return; }
}

