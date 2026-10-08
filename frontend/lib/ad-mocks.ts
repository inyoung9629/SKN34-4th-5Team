import type { AdCreative, AdDelivery, AdPlacement } from "./ads";

export const mockAds: AdCreative[] = [
  {
    id: 1, advertiser: "구단 광고 미리보기", title: "응원하는 팀의 새로운 소식을 만나보세요",
    description: "구단 행사·굿즈·예매 안내가 표시될 자리입니다.", image_url: "/images/ads/lotte-giants.jpeg", image_alt: "롯데 자이언츠 로고",
    destination_url: "https://www.instagram.com/busanlottegiants/", button_label: "인스타그램 보기", context_label: "구단 소식",
    placement: "home-club-banner", starts_at: "2020-01-01T00:00:00+09:00", ends_at: "2100-01-01T00:00:00+09:00", active: true,
  },
  {
    id: 2, advertiser: "음식점·시설 광고 미리보기", title: "직관 전후, 함께 들를 곳을 찾아보세요",
    description: "추천 루트 주변의 음식점·시설 광고가 표시될 자리입니다.", image_url: "/images/ads/local-menu.jpeg", image_alt: "예시 광고 가게의 메뉴 사진",
    destination_url: "https://naver.me/xSnyHYCQ", button_label: "스토어 보기", context_label: "음식점·시설",
    placement: "home-route-partner-banner", starts_at: "2020-01-01T00:00:00+09:00", ends_at: "2100-01-01T00:00:00+09:00", active: true,
  },
];

export function getMockDelivery(placement: AdPlacement): AdDelivery {
  const ad = mockAds.find(item => item.placement === placement) ?? null;
  return { ad, token: "", exposure_id: null, server_now: new Date().toISOString(), valid_until: ad?.ends_at ?? null, preview: true };
}
