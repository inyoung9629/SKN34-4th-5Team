// Mock advertisements only. Official source URLs and team relationships were checked on 2026-10-02.
// Group by the advertised supplier brand, not by distributor or contract manufacturer.
export type SupplierAd = {
  id: string; name: string; teams: string[]; teamCodes: string[]; color: string;
  product: string; href: string; sourceImage: string;
};

export function uniqueSupplierAds(rows: readonly SupplierAd[]): SupplierAd[] {
  const brands = new Map<string, SupplierAd>();
  for (const row of rows) {
    const key = row.id.trim().toLowerCase();
    const existing = brands.get(key);
    if (existing) {
      existing.teams = [...new Set([...existing.teams, ...row.teams])];
      existing.teamCodes = [...new Set([...existing.teamCodes, ...row.teamCodes])];
    } else brands.set(key, { ...row, id: key, teams: [...row.teams], teamCodes: [...row.teamCodes] });
  }
  return [...brands.values()];
}

export const supplierAds = uniqueSupplierAds([
  {
    "id": "prospecs",
    "name": "프로스펙스",
    "teams": [
      "LG 트윈스"
    ],
    "teamCodes": [
      "LG"
    ],
    "color": "#A50034",
    "product": "2026 LG 어센틱 홈 유니폼",
    "href": "https://www.prospecs.com/planning.do?cmd=getPlanningDetail&datacls=5271",
    "sourceImage": "https://img.prospecs.com/prod/PP3LT26/PP3LT26M011/PP3LT26M011_01.jpg/dims/resizef/1080x1080/optimize"
  },
  {
    "id": "spyder",
    "name": "스파이더",
    "teams": [
      "한화 이글스"
    ],
    "teamCodes": [
      "HH"
    ],
    "color": "#D95714",
    "product": "한화 레터맨 바시티 자켓",
    "href": "https://m.spyder.co.kr/skin-skin11/product/list.html?cate_no=1118&sort_method=4",
    "sourceImage": "https://m.spyder.co.kr/web/product/medium/202602/ca962f2dbeb8d227532be52a601615df.jpg"
  },
  {
    "id": "dynafit",
    "name": "다이나핏",
    "teams": [
      "SSG 랜더스"
    ],
    "teamCodes": [
      "SK"
    ],
    "color": "#324456",
    "product": "TEAM 미들 다운 베스트",
    "href": "https://www.k-village.co.kr/goods/YUW26557Z1",
    "sourceImage": "https://contents.k-village.co.kr/Prod/2026/Y/YUW26557Z1/YUW26557Z1_DG_01.JPG/k2dims/resize/540/extent/540x720/optimize"
  },
  {
    "id": "under-armour",
    "name": "언더아머",
    "teams": [
      "삼성 라이온즈"
    ],
    "teamCodes": [
      "SS"
    ],
    "color": "#1462A2",
    "product": "퍼포먼스 테크 크루 삭스",
    "href": "https://www.underarmour.co.kr/ko-kr/p/양말/ua_퍼포먼스_테크/6015141.html",
    "sourceImage": "https://underarmour.scene7.com/is/image/Underarmour/6015141-008_PACK_SL?bgc=f0f0f0&hei=1000&op_usm=1.75%2C0.3%2C2%2C0&qlt=85&rp=standard-0pad%7Cpdp&wid=800"
  },
  {
    "id": "reebok",
    "name": "리복",
    "teams": [
      "NC 다이노스"
    ],
    "teamCodes": [
      "NC"
    ],
    "color": "#235378",
    "product": "NC 시그니처 얼트 홈 유니폼",
    "href": "https://store.ncdinos.com/index.html",
    "sourceImage": "https://store.ncdinos.com/web/product/medium/202607/1c9a752c3ed34ba31bdb14765cc4ade6.jpg"
  },
  {
    "id": "new-balance",
    "name": "뉴발란스",
    "teams": [
      "KT 위즈"
    ],
    "teamCodes": [
      "KT"
    ],
    "color": "#A91F35",
    "product": "2026 KT 어센틱 홈 유니폼",
    "href": "https://www.ktwizstore.co.kr/category/유니폼/85/",
    "sourceImage": "https://ecimg.cafe24img.com/pg2623b60374486020/ktwiz111/web/product/medium/20260323/39e54c09a4104d149301ad878dd7ddbb.jpg"
  },
  {
    "id": "willbe-play",
    "name": "윌비플레이",
    "teams": [
      "롯데 자이언츠"
    ],
    "teamCodes": [
      "LT"
    ],
    "color": "#A33347",
    "product": "PLAY 2 저지 블랙",
    "href": "https://wb-play.co.kr",
    "sourceImage": "https://ecimg.cafe24img.com/pg666b47713532098/wbplay/web/product/medium/20260610/019ac8a327f0aae5396c10a63cf52b04.jpg"
  },
  {
    "id": "iab-studio",
    "name": "아이앱 스튜디오",
    "teams": [
      "KIA 타이거즈"
    ],
    "teamCodes": [
      "HT"
    ],
    "color": "#9A3033",
    "product": "Surfing Bone 티셔츠",
    "href": "https://iabstudio.theshop.jp/items/154680998",
    "sourceImage": "https://baseec-img-mng.akamaized.net/images/item/origin/2553abd2da4a9b1a2329ccdf15eec7a6.jpg?imformat=generic&q=90&im=Resize,width=640,type=normal"
  },
  {
    "id": "adidas",
    "name": "아디다스",
    "teams": [
      "두산 베어스"
    ],
    "teamCodes": [
      "OB"
    ],
    "color": "#27375D",
    "product": "어센틱 아듀잠실 유니폼",
    "href": "https://www.doosanbearswefan.shop/goods/goods_view.php?goodsNo=1000000417",
    "sourceImage": "https://godomall-storage.cdn-nhncommerce.com/aee175be62cdbd8420a72c0186e673b8/goods/1000000417/image/main/1000000417_main_032.jpg"
  },
  {
    "id": "nike",
    "name": "나이키",
    "teams": [
      "키움 히어로즈"
    ],
    "teamCodes": [
      "WO"
    ],
    "color": "#62334B",
    "product": "Alpha 2.0 배팅 장갑",
    "href": "https://www.nike.com/w/baseball-gloves-1toquz3rauvz5e1x6z99fch",
    "sourceImage": "https://static.nike.com/a/images/t_web_pw_592_v2/f_auto/u_9ddf04c7-2a9a-4d76-add1-d15af8f0263d,c_scale,fl_relative,w_1.0,h_1.0,fl_layer_apply/fb9569ef-943a-4b7d-b716-82992b9a9c33/NIKE+ALPHA+2.0+BG.png"
  }
]);

export function nextAdIndex(index: number, count: number, direction = 1) {
  return count > 0 ? ((index + direction) % count + count) % count : 0;
}
export function randomAdIndex(count: number, random = Math.random) {
  return Math.min(Math.max(0, count - 1), Math.max(0, Math.floor(random() * count)));
}

