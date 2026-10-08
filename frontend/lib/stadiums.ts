import type { StadiumParkingMap } from "./stadium-parking-maps";
import { stadiumLocationAudit } from "./stadium-locations";

export type Stadium = {
  code: string;
  name: string;
  region: string;
  address: string;
  lng: number;
  lat: number;
  color: string;
  teams: string[];
  photoPosition?: string;
  parkingMap?: StadiumParkingMap;
  seatingMap?: {
    src: string;
    sourceUrl: string;
    season: number;
    teamCode: string;
  };
  cardImage?: {
    src: string;
    sourceUrl: string;
    credit: string;
    creditUrl: string;
    licenseUrl?: string;
    objectPosition?: string;
  };
};

// Address-geocoded baseline retained for auditing, not for current map pins.
export const addressGeocodedStadiums: Stadium[] = [
  { code: "JAMSIL", name: "잠실야구장", region: "서울", address: "서울특별시 송파구 올림픽로 25", lng: 127.075940589715, lat: 37.5161987797456, color: "blue", teams: ["LG 트윈스", "두산 베어스"], photoPosition: "center 48%" },
  { code: "GOCHEOK", name: "고척스카이돔", region: "서울", address: "서울특별시 구로구 경인로 430", lng: 126.867088741096, lat: 37.4982125677913, color: "violet", teams: ["키움 히어로즈"], photoPosition: "center 55%" },
  { code: "MUNHAK", name: "인천 SSG 랜더스필드", region: "인천·경기", address: "인천광역시 미추홀구 매소홀로 618", lng: 126.690759830613, lat: 37.4350819826381, color: "rose", teams: ["SSG 랜더스"], photoPosition: "center 67%" },
  { code: "SUWON", name: "수원 KT 위즈 파크", region: "인천·경기", address: "경기도 수원시 장안구 경수대로 893", lng: 127.011348102567, lat: 37.2978428909635, color: "blue", teams: ["KT 위즈"], photoPosition: "center 40%" },
  { code: "DAEJEON", name: "대전 한화생명 볼파크", region: "대전·광주", address: "대전광역시 중구 대종로 373", lng: 127.428013823451, lat: 36.3173370007388, color: "orange", teams: ["한화 이글스"], photoPosition: "center 43%" },
  { code: "DAEGU", name: "대구 삼성 라이온즈 파크", region: "대구·부산·창원", address: "대구광역시 수성구 야구전설로 1", lng: 128.681236372268, lat: 35.8411289243023, color: "blue", teams: ["삼성 라이온즈"], photoPosition: "center 47%" },
  { code: "GWANGJU", name: "광주-KIA 챔피언스 필드", region: "대전·광주", address: "전남광주통합특별시 북구 서림로 10", lng: 126.888805470329, lat: 35.1694249627659, color: "rose", teams: ["KIA 타이거즈"], photoPosition: "center 47%" },
  { code: "SAJIK", name: "사직야구장", region: "대구·부산·창원", address: "부산광역시 동래구 사직로 45", lng: 129.059900885997, lat: 35.194366802896, color: "orange", teams: ["롯데 자이언츠"], photoPosition: "center 50%" },
  { code: "CHANGWON", name: "창원 NC 파크", region: "대구·부산·창원", address: "경상남도 창원시 마산회원구 삼호로 63", lng: 128.579580117268, lat: 35.2219848625101, color: "green", teams: ["NC 다이노스"], photoPosition: "center 53%" },
];

// Explicit first-team venue whitelist. Never substitute a nearby youth/futures venue.
export const stadiums: Stadium[] = addressGeocodedStadiums.map(stadium => {
  const point = stadiumLocationAudit.stadiums[stadium.code as keyof typeof stadiumLocationAudit.stadiums];
  return point ? { ...stadium, lat: point.lat, lng: point.lng } : stadium;
});

export function getStadium(code: string) {
  return stadiums.find((stadium) => stadium.code === code);
}

export function getStadiumMapUrl(stadium: Stadium) {
  return `https://map.kakao.com/link/map/${encodeURIComponent(stadium.name)},${stadium.lat},${stadium.lng}`;
}
