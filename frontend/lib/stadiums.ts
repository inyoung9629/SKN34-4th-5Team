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
  seatingMap: {
    src: string;
    sourceUrl: string;
  };
  cardImage: {
    src: string;
    sourceUrl: string;
    credit: string;
    creditUrl: string;
    objectPosition?: string;
  };
};

// Address-geocoded baseline retained for auditing, not for current map pins.
export const addressGeocodedStadiums: Stadium[] = [
  { code: "JAMSIL", name: "잠실야구장", region: "서울", address: "서울특별시 송파구 올림픽로 25", lng: 127.075940589715, lat: 37.5161987797456, color: "blue", teams: ["LG 트윈스", "두산 베어스"], seatingMap: { src: "/images/stadiums/seating-maps/jamsil.png", sourceUrl: "https://www.lgtwins.com/ticket/general" }, cardImage: { src: "/images/stadiums/exteriors/jamsil.jpg", sourceUrl: "https://commons.wikimedia.org/wiki/File:Jamsil_Baseball_Stadium_Seoul.jpg", credit: "Arne Müseler · CC BY-SA 3.0", creditUrl: "https://creativecommons.org/licenses/by-sa/3.0/de/deed.en", objectPosition: "center 48%" } },
  { code: "GOCHEOK", name: "고척스카이돔", region: "서울", address: "서울특별시 구로구 경인로 430", lng: 126.867088741096, lat: 37.4982125677913, color: "violet", teams: ["키움 히어로즈"], seatingMap: { src: "/images/stadiums/seating-maps/gocheok.jpg", sourceUrl: "https://heroesbaseball.co.kr/mobile/ticket/normal/view.do" }, cardImage: { src: "/images/stadiums/exteriors/gocheok.jpg", sourceUrl: "https://culture.seoul.go.kr/night/sub/viewSpot/view.do?viewId=55", credit: "서울문화포털", creditUrl: "https://culture.seoul.go.kr/night/sub/viewSpot/view.do?viewId=55", objectPosition: "center 55%" } },
  { code: "MUNHAK", name: "인천 SSG 랜더스필드", region: "인천·경기", address: "인천광역시 미추홀구 매소홀로 618", lng: 126.690759830613, lat: 37.4350819826381, color: "rose", teams: ["SSG 랜더스"], seatingMap: { src: "/images/stadiums/seating-maps/munhak.png", sourceUrl: "https://www.ssglanders.com/game/ticket" }, cardImage: { src: "/images/stadiums/exteriors/munhak-exterior.jpg", sourceUrl: "https://commons.wikimedia.org/wiki/File:SSG_%EB%9E%9C%EB%8D%94%EC%8A%A4%ED%95%84%EB%93%9C_%EC%A0%84%EA%B2%BD_2024.jpg", credit: "Narubaru7 · CC BY 4.0", creditUrl: "https://creativecommons.org/licenses/by/4.0/", objectPosition: "center 67%" } },
  { code: "SUWON", name: "수원 KT 위즈 파크", region: "인천·경기", address: "경기도 수원시 장안구 경수대로 893", lng: 127.011348102567, lat: 37.2978428909635, color: "blue", teams: ["KT 위즈"], seatingMap: { src: "/images/stadiums/seating-maps/suwon.jpg", sourceUrl: "https://www.ktwiz.co.kr/wizpark/guide" }, cardImage: { src: "/images/stadiums/exteriors/suwon.jpg", sourceUrl: "https://commons.wikimedia.org/wiki/File:Suwon_kt_wiz_Park.jpg", credit: "Nt · CC BY 4.0", creditUrl: "https://creativecommons.org/licenses/by/4.0/", objectPosition: "center 40%" } },
  { code: "DAEJEON", name: "대전 한화생명 볼파크", region: "대전·광주", address: "대전광역시 중구 대종로 373", lng: 127.428013823451, lat: 36.3173370007388, color: "orange", teams: ["한화 이글스"], seatingMap: { src: "/images/stadiums/seating-maps/daejeon.png", sourceUrl: "https://www.hanwhaeagles.co.kr/ticketInfo.do" }, cardImage: { src: "/images/stadiums/exteriors/daejeon.jpg", sourceUrl: "https://www.hanwhaeagles.co.kr/MN/EP/MNEPPI01.do", credit: "한화이글스", creditUrl: "https://www.hanwhaeagles.co.kr/MN/EP/MNEPPI01.do", objectPosition: "center 43%" } },
  { code: "DAEGU", name: "대구 삼성 라이온즈 파크", region: "대구·부산·창원", address: "대구광역시 수성구 야구전설로 1", lng: 128.681236372268, lat: 35.8411289243023, color: "blue", teams: ["삼성 라이온즈"], seatingMap: { src: "/images/stadiums/seating-maps/daegu.jpg", sourceUrl: "https://www.samsunglions.com/intro/intro05_4.asp" }, cardImage: { src: "/images/stadiums/exteriors/daegu.jpg", sourceUrl: "https://commons.wikimedia.org/wiki/File:Daegu_Samseong_Lions_Park.jpg", credit: "Neoalpha · CC0", creditUrl: "https://creativecommons.org/publicdomain/zero/1.0/", objectPosition: "center 47%" } },
  { code: "GWANGJU", name: "광주-KIA 챔피언스 필드", region: "대전·광주", address: "전남광주통합특별시 북구 서림로 10", lng: 126.888805470329, lat: 35.1694249627659, color: "rose", teams: ["KIA 타이거즈"], seatingMap: { src: "/images/stadiums/seating-maps/gwangju.png", sourceUrl: "https://tigers.co.kr/tigers/champions-field/stadium-guide" }, cardImage: { src: "/images/stadiums/exteriors/gwangju.png", sourceUrl: "https://commons.wikimedia.org/wiki/File:20250628_Gwangju-Kia_Champions_Field_Jjw_001.png", credit: "Jjw · CC BY-SA 4.0", creditUrl: "https://creativecommons.org/licenses/by-sa/4.0/", objectPosition: "center 47%" } },
  { code: "SAJIK", name: "사직야구장", region: "대구·부산·창원", address: "부산광역시 동래구 사직로 45", lng: 129.059900885997, lat: 35.194366802896, color: "orange", teams: ["롯데 자이언츠"], seatingMap: { src: "/images/stadiums/seating-maps/sajik.jpg", sourceUrl: "https://www.giantsclub.com/html/?pcode=340" }, cardImage: { src: "/images/stadiums/exteriors/sajik.gif", sourceUrl: "https://www.giantsclub.com/html/?pcode=207", credit: "롯데자이언츠", creditUrl: "https://www.giantsclub.com/html/?pcode=207", objectPosition: "center 50%" } },
  { code: "CHANGWON", name: "창원 NC 파크", region: "대구·부산·창원", address: "경상남도 창원시 마산회원구 삼호로 63", lng: 128.579580117268, lat: 35.2219848625101, color: "green", teams: ["NC 다이노스"], seatingMap: { src: "/images/stadiums/seating-maps/changwon.jpg", sourceUrl: "https://www.ncdinos.com/dinos/stadium.do" }, cardImage: { src: "/images/stadiums/exteriors/changwon.png", sourceUrl: "https://www.ncdinos.com/dinos/stadium.do", credit: "NC 다이노스", creditUrl: "https://www.ncdinos.com/dinos/stadium.do", objectPosition: "center 53%" } },
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
