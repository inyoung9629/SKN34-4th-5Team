export const CLUB_AD_INTERVAL_MS = 5_000;

// Official SNS destinations; source notes: public/images/ads/clubs/README.md.
export const clubAds = [
  { code: "LG", name: "LG 트윈스", english: "LG TWINS", color: "#A50034", dark: "#270C1C", instagram: "lgtwinsbaseballclub" },
  { code: "HH", name: "한화 이글스", english: "HANWHA EAGLES", color: "#DC4B08", dark: "#35160B", instagram: "hanwhaeagles_soori" },
  { code: "SK", name: "SSG 랜더스", english: "SSG LANDERS", color: "#B51B35", dark: "#28111C", instagram: "ssglanders.incheon" },
  { code: "SS", name: "삼성 라이온즈", english: "SAMSUNG LIONS", color: "#1264CE", dark: "#071F53", instagram: "samsunglions_baseballclub" },
  { code: "NC", name: "NC 다이노스", english: "NC DINOS", color: "#2768A0", dark: "#0A203B", instagram: "ncdinos2011" },
  { code: "KT", name: "KT 위즈", english: "KT WIZ", color: "#AB1830", dark: "#14151C", instagram: "ktwiz.pr" },
  { code: "LT", name: "롯데 자이언츠", english: "LOTTE GIANTS", color: "#B92542", dark: "#071D3D", instagram: "busanlottegiants" },
  { code: "HT", name: "KIA 타이거즈", english: "KIA TIGERS", color: "#C61935", dark: "#2C1020", instagram: "always_kia_tigers" },
  { code: "OB", name: "두산 베어스", english: "DOOSAN BEARS", color: "#303D75", dark: "#11152E", instagram: "doosanbears.1982" },
  { code: "WO", name: "키움 히어로즈", english: "KIWOOM HEROES", color: "#862040", dark: "#300D24", instagram: "heroesbaseballclub" },
] as const;

export function clubInstagramUrl(index: number) {
  return `https://www.instagram.com/${clubAds[index].instagram}/`;
}

export function randomClubIndex(random = Math.random) {
  return Math.min(clubAds.length - 1, Math.max(0, Math.floor(random() * clubAds.length)));
}

export function nextClubIndex(index: number, direction = 1) {
  return ((index + direction) % clubAds.length + clubAds.length) % clubAds.length;
}

// Pure timer lifecycle, also tested with a virtual clock.
export function startClubRotation(advance: () => void) {
  const timer = setInterval(advance, CLUB_AD_INTERVAL_MS);
  return () => clearInterval(timer);
}
