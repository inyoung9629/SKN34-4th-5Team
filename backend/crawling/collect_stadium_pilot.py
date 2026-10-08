"""One-stadium public-data collection and transient Kakao coverage comparison.

Uses only the Python standard library. Credentials are read locally and never logged.
"""
import argparse
import json
import math
import re
import time
import sys
import unicodedata
from datetime import datetime, timezone
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
PARK = "https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api"
TOUR = "https://apis.data.go.kr/B551011/KorService2/locationBasedList2"
KAKAO = "https://dapi.kakao.com/v2/local/search/category.json"
CONVENIENCE_BRANDS = ("CU", "GS25", "세븐일레븐", "이마트24")


def distance(lat, lng, lat2, lng2):
    a, b, c, d = map(math.radians, (lat, lng, lat2, lng2))
    h = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, h)))


def page_items(payload):
    response = payload.get("response", payload)
    header = response.get("header", {})
    code = str(header.get("resultCode", ""))
    if code not in ("00", "0000"):
        raise RuntimeError("Provider rejected request; resultCode=" + re.sub(r"[^A-Za-z0-9_]", "", code)[:40])
    body = response.get("body", {})
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise RuntimeError("Unexpected items format")
    return items, int(body.get("totalCount", len(items)))


def all_pages(url, params, start=1, size=1000, header_out=None):
    rows, seen = [], set()
    total = None
    for page in range(start, start+100):
        payload = get(url, {**params, "pageNo": page, "numOfRows": size})
        if header_out is not None:
            header_out.update(payload.get("response",payload).get("header",{}))
        items, expected = page_items(payload)
        if total is not None and expected != total:
            raise RuntimeError("Total count changed during pagination; rerun snapshot")
        total = expected
        if not items and len(rows) < total:
            raise RuntimeError("Incomplete pagination")
        signature = json.dumps(items, sort_keys=True, ensure_ascii=False)
        if items and signature in seen:
            raise RuntimeError("Provider repeated a page")
        seen.add(signature)
        rows.extend(items)
        if len(rows) >= total:
            return rows
        time.sleep(.15)
    raise RuntimeError("Pagination limit reached")


def record(source, ident, name, kind, address, lat, lng, stadium, raw):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat)>90 or abs(lng)>180:
        return None
    meters = distance(stadium[0], stadium[1], lat, lng)
    if meters > 2500 or not ident or not name:
        return None
    return dict(source=source, source_id=str(ident), name=name, kind=kind,
                address=address, lat=lat, lng=lng, distance_m=round(meters, 1),
                source_fields=raw)


def collect_sbiz(key, stadium):
    """The retired source must never be collected again through legacy callers."""
    raise RuntimeError("SBIZ collection was removed; use the current PARK/TOUR collector")


def collect_parks(key, stadium):
    rows = all_pages(PARK, {"serviceKey":unquote(key),"type":"json"}, start=1)
    result = []
    for r in rows:
        # Standard-data endpoints use camelCase in JSON responses.
        p = record("PARK",r.get("manageNo") or r.get("MANAGE_NO"),r.get("parkNm") or r.get("PARK_NM"),"walk",
                   r.get("rdnmadr") or r.get("lnmadr") or r.get("RDNMADR"),
                   r.get("latitude") or r.get("LATITUDE"),r.get("longitude") or r.get("LONGITUDE"),stadium,r)
        if p:
            result.append(p)
    return result, {"api_rows":len(rows),"note":"Park representative coordinate, not trail geometry or entrance"}


def collect_tour(key, stadium):
    params = {"serviceKey":unquote(key),"MobileOS":"ETC","MobileApp":"KBORoute","_type":"json"}
    rows = all_pages(TOUR, {**params,"mapX":stadium[1],"mapY":stadium[0],"radius":2500,"arrange":"E"},size=100)
    result = []
    for r in rows:
        ct = str(r.get("contenttypeid"))
        kind = {"12":"sight","14":"sight","32":"lodging","39":"restaurant"}.get(ct)
        if re.search("공원|산책|둘레길|숲길|수목원",r.get("title", "")) and ct in ("12","28"):
            kind = "walk"
        if ct == "39" and (r.get("cat3") == "A05020900" or re.search("카페|커피",r.get("title", ""))):
            kind = "cafe"
        if play_type(r.get("title", "")):
            kind = "play_facility"
        if not kind:
            continue
        p = record("TOUR",r.get("contentid"),r.get("title"),kind,r.get("addr1"),r.get("mapy"),r.get("mapx"),stadium,r)
        if p:
            result.append(p)
    def enrich(p):
        errors = []
        for endpoint in ("detailCommon2","detailIntro2"):
            query = {**params,"contentId":p["source_id"],"contentTypeId":p["source_fields"]["contenttypeid"]}
            if endpoint == "detailCommon2":
                query.pop("contentTypeId")
            try:
                items, _ = page_items(get(TOUR.rsplit("/",1)[0]+"/"+endpoint,query))
                p[endpoint] = items
            except RuntimeError as e:
                errors.append({"endpoint":endpoint,"error":str(e)})
        if errors:
            p["detail_errors"] = errors
        return p
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(enrich,result))
    return result, {"api_rows":len(rows),"detail_error_places":sum(bool(p.get("detail_errors")) for p in result)}


def compact(value):
    return re.sub(r"[^가-힣a-z0-9]", "", str(value).lower())


def play_type(name, category=""):
    """Classify explicit facility types, not generic words like kids or board."""
    text = compact(name) + " " + compact(category)
    if any(word in text for word in ("키즈카페", "실내놀이터")):
        return "kids_cafe"
    if any(word in text for word in ("보드게임카페", "보드카페")):
        return "board_game_cafe"
    if "보드게임" in text and "오락장" in category:
        return "board_game_cafe"
    return None


def convenience_brand(name):
    """Normalize explicit brand names; corporate/legacy names remain review candidates."""
    normalized = unicodedata.normalize("NFKC", str(name)).lower()
    text = compact(normalized)
    patterns = (
        ("CU", r"씨\s*유|(?<![a-z])c\s*u(?![a-z])"),
        ("GS25", r"gs25|지에스25|지에스이십오"),
        ("세븐일레븐", r"세븐일레븐|쎄븐일레븐|7eleven|seveneleven"),
        ("이마트24", r"이마트24|이마트이십사|emart24"),
    )
    matches = [brand for brand, pattern in patterns
               if re.search(pattern, normalized if brand == "CU" else text)]
    if len(matches) == 1:
        return {"brand": matches[0], "brand_status": "name_identified"}
    if len(matches) > 1:
        return {"brand": None, "brand_status": "needs_review", "brand_candidates": matches}
    if "미니스톱" in text or "위드미" in text:
        return {"brand": None, "brand_status": "legacy_name"}
    for brand, pattern in (
        ("세븐일레븐", r"코리아세븐|^세븐.+코리아$|세브일레븐"),
        ("GS25", r"^지에스"),
        ("CU", r"비지에프리테일"),
    ):
        if re.search(pattern, text):
            return {"brand": None, "brand_status": "needs_review", "brand_candidates": [brand]}
    return {"brand": None, "brand_status": "unidentified"}


def classify_place(place):
    raw = place.get("source_fields", {})
    if place.get("source") == "SBIZ" and raw.get("indsLclsCd") == "G2" and raw.get("indsSclsNm") == "편의점":
        return {**place, "kind": "convenience_store", **convenience_brand(place["name"]),
                "classification_basis": "sbiz_convenience_store_category"}
    subtype = play_type(place["name"], raw.get("indsSclsNm", ""))
    if subtype:
        return {**place, "kind": "play_facility", "play_type": subtype,
                "classification_basis": "explicit_name_or_source_category"}
    return place


def road_building(value):
    match = re.search(r"\S+(?:로|길)\s+\d+(?:-\d+)?",str(value))
    return compact(match[0]) if match else ""


def compare_kakao(key, sbiz, stadium):
    docs = {}
    for category in ("FD6","CE7"):
        for sort in ("distance", "accuracy"):
            for page in range(1,4):
                payload = get(KAKAO,{"category_group_code":category,"x":stadium[1],"y":stadium[0],
                                     "radius":2500,"sort":sort,"page":page,"size":15},
                              {"Authorization":"KakaoAK "+key})
                for d in payload.get("documents",[]):
                    if distance(stadium[0],stadium[1],float(d["y"]),float(d["x"])) <= 2500:
                        docs[d["id"]] = d
                if payload.get("meta",{}).get("is_end"):
                    break
                time.sleep(.15)
    findings = []
    prepared = [(s,compact(s["name"]),road_building(s["address"])) for s in sbiz]
    for ident,d in docs.items():
        name = compact(d["place_name"])
        address = road_building(d.get("road_address_name",""))
        candidates = []
        for s,sname,saddress in prepared:
            meters = distance(float(d["y"]),float(d["x"]),s["lat"],s["lng"])
            same_address = bool(address and address == saddress)
            if meters > 150 and name != sname and not same_address:
                continue
            sim = SequenceMatcher(None,name,sname).ratio()
            if meters <= 150 and sim >= .45 or name == sname or same_address:
                candidates.append((sim,meters,s))
        candidates.sort(key=lambda c:(-c[0],c[1]))
        status = "not_found_in_snapshot"
        matches = []
        if candidates:
            sim,meters,s = candidates[0]
            status = "likely_match" if sim >= .8 and meters <= 100 else "manual_review"
            matches = [{"sbiz_id":p["source_id"],"sbiz_name":p["name"],"name_similarity":round(score,3),
                        "coordinate_gap_m":round(gap,1)} for score,gap,p in candidates[:3]]
        # Keep IDs, links and audit results, not Kakao place content or RAG documents.
        finding = {"kakao_id":ident,"kakao_url":d["place_url"],"status":status,"sbiz_candidates":matches}
        subtype = play_type(d["place_name"], d.get("category_name", ""))
        if subtype:
            finding["kind"] = "play_facility"
            finding["play_type"] = subtype
        findings.append(finding)
    return {"sample_size":len(findings),"counts":dict(Counter(f["status"] for f in findings)),"findings":findings,
            "method":"FD6/CE7, distance and accuracy, max 45 each; sample only, not population coverage",
            "caution":"Nonmatch is not proof of absence; names, coordinates and snapshot dates may differ"}


def write_report(out, summary):
    combined, convenience_stores = [], []
    for source in ("sbiz","park","tour"):
        path = out/(source+".json")
        if path.exists():
            for p in map(classify_place, json.loads(path.read_text(encoding="utf-8"))):
                if p["kind"] == "convenience_store":
                    convenience_stores.append(p)
                    if p["brand_status"] != "name_identified":
                        continue
                if (p["kind"] in {"restaurant","cafe","walk","sight","lodging","play_facility","convenience_store"}
                        and p["source_fields"].get("indsSclsNm") != "기숙사/고시원"):
                    combined.append(p)
    (out/"convenience_stores.json").write_text(json.dumps(convenience_stores,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"places.json").write_text(json.dumps(combined,ensure_ascii=False,indent=2),encoding="utf-8")
    convenience_counts = Counter(p["brand_status"] for p in convenience_stores)
    brand_counts = Counter(p["brand"] for p in convenience_stores if p["brand_status"] == "name_identified")
    counts = Counter((p["source"],p["kind"]) for p in combined)
    summary["places"] = {"records": len(combined),
                         "categories": dict(Counter(p["kind"] for p in combined)),
                         "classification_policy": "play_facilities_and_major_brand_convenience_stores"}
    summary["convenience_stores"] = {
        "source_records": len(convenience_stores), "included_records": sum(brand_counts.values()),
        "by_brand": dict(brand_counts), "brand_statuses": dict(convenience_counts),
        "scope": list(CONVENIENCE_BRANDS),
        "note": "Brand name classification only; not deduplicated or verified as currently operating",
    }
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    lines = ["# 잠실구장 2.5km 장소 수집 결과", "",
             f"수집 시작(UTC): {summary['collected_at']}. 중심 좌표: {summary['center']}. 직선 반경 2,500m.","",
             "## 수집 건수", "", "| 출처 | 식당·음식 업종 | 카페 | 산책·공원 | 볼거리 | 숙박 후보 | 놀이시설 | 편의점 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for source in ("SBIZ","PARK","TOUR"):
        lines.append("| "+source+" | "+" | ".join(str(counts[(source,k)]) for k in ("restaurant","cafe","walk","sight","lodging","play_facility","convenience_store"))+" |")
    lines += ["", "출처 간 중복 제거 전 건수이며 실제 영업·예약 가능 여부를 검증한 수치가 아닙니다.",
              f"소상공인 원본 기준월: {summary.get('sources',{}).get('SBIZ',{}).get('reference_month','미확인')}. 수집 시각과 원본 기준일은 다릅니다.",
              "소상공인 음식 업종에는 빵집·떡집·구내식당 등이 포함됩니다. 숙박 후보에서는 기숙사/고시원을 제외했습니다.",
              "공원은 대표 좌표 기준입니다. 실제 출입구·산책로 선형·도보거리를 뜻하지 않습니다.",
              "TourAPI 상세소개·메뉴·영업시간 등은 제공된 항목만 저장했으며 빈 값을 추정해 채우지 않았습니다.",
              "보드게임 카페와 키즈카페·실내놀이터는 놀이시설(play_facility)로 분류합니다. 일반 카페와 중복 분류하지 않습니다.",
              "명칭·업종에 구체적인 시설 유형이 있는 경우에 적용하며, 키즈/보드라는 단어만으로 분류하지 않습니다.",
              "원본 업종은 보존하고 places.json에서 분류합니다. 웹 검증 후보의 분류와 점포 연결 여부는 별개입니다.",
              "주점과 기타 업종도 누락 대조를 위해 sbiz.json에 보존했습니다. 놀이시설 또는 주요 브랜드 편의점으로 분류된 장소를 제외한 나머지는 places.json에서 제외했습니다.", "",
              "## 편의점 분류", "",
              "편의점은 convenience_store로 독립 분류합니다. 원본 업종은 소매 > 종합 소매 > 편의점입니다.",
              "1차 장소 목록에는 CU·GS25·세븐일레븐·이마트24의 브랜드명이 명시된 기록을 포함합니다.",
              "브랜드명 식별은 실제 영업 확인이나 동일 점포 중복 제거를 뜻하지 않습니다.", "",
              "| 브랜드 | 명칭으로 식별된 기록 |", "|---|---:|"]
    lines += [f"| {brand} | {brand_counts[brand]} |" for brand in CONVENIENCE_BRANDS]
    lines += ["", f"편의점 원본 {len(convenience_stores)}건 중 장소 목록에 포함 {sum(brand_counts.values())}건.",
              f"브랜드 검토 필요 {convenience_counts['needs_review']}건, 과거 브랜드 표기 {convenience_counts['legacy_name']}건, 브랜드 미식별 {convenience_counts['unidentified']}건은 검토 자료로 보존합니다.",
              "법인명·축약명·오타는 brand_candidates에 후보만 기록하며, 미니스톱·위드미의 현재 브랜드를 자동 확정하지 않습니다.",
              "[편의점 전체 분류·검토 자료](convenience_stores.json)에서 포함 기록과 검토 후보를 함께 확인할 수 있습니다.", "",
              "## 데이터 파일", "", "- [사용할 7종 장소 데이터](places.json)",
              "- [소상공인 전체 업종 대조용 원본](sbiz.json)","- [공원 상세](park.json)",
              "- [TourAPI 목록·상세](tour.json)","- [실행 요약](summary.json)","",
              "## 카카오맵 표본 비교", ""]
    compare = out/"kakao_comparison.json"
    if compare.exists():
        data = json.loads(compare.read_text(encoding="utf-8"))
        counts = data["counts"]
        lines += [f"표본 {data['sample_size']}곳: 일치 가능성 높음 {counts.get('likely_match',0)}, 수동 확인 {counts.get('manual_review',0)}, 대응 후보 없음 {counts.get('not_found_in_snapshot',0)}.","",
                  "식당·카페 검색에서 거리순/정확도순 각각 최대 45건을 조회한 표본입니다. 구장 내부 매점이 포함될 수 있고 전체 누락률을 추정할 수 없습니다.",
                  "이름 유사도·좌표 거리·도로명 건물주소로 소상공인 전체 업종과 대조했습니다. 동일 건물은 동일 가게임을 보장하지 않습니다.",
                  "대응 후보 없음은 해당 반경 스냅샷에서 자동 매칭되지 않았다는 뜻이며 전국 DB의 완전한 부재나 신규 개업을 확정하지 않습니다.",
                  "카카오 응답 내용은 비교 중 메모리에서만 사용했습니다. 아래에는 장소 ID·URL 및 매칭 결과만 남겼으며 RAG 데이터에 넣지 않았습니다.","",
                  "### 대응 후보를 찾지 못한 장소", ""]
        for f in data["findings"]:
            if f["status"] == "not_found_in_snapshot":
                lines.append(f"- [카카오 장소 {f['kakao_id']}]({f['kakao_url']})")
        lines += ["", "[전체 비교 결과와 소상공인 후보](kakao_comparison.json)", ""]
    else:
        lines.append("비교 미완료: summary.json의 comparison 상태를 확인하세요.")
    verification = out/"web_verification.json"
    if verification.exists():
        audit = json.loads(verification.read_text(encoding="utf-8"))
        lines += ["", "## 후속 웹 검증", "",
                  "[미확정 후보 웹 검증 보고서](WEB_VERIFICATION.md) · [판정과 근거](web_verification.json)",
                  "위 자동 매칭 수치는 최초 실행 결과입니다. 후속 검토: " + json.dumps(audit["counts"], ensure_ascii=False),
                  "웹 검증에서 분류한 놀이시설은 점포 연결이 미확정이어도 분류를 유지합니다. 분류 변경은 연결 확인이나 영업 확인을 뜻하지 않습니다.", ""]
    lines += ["## 출처", "", "- [소상공인 상가정보](https://www.data.go.kr/data/15012005/openapi.do)",
              "- [전국도시공원정보](https://www.data.go.kr/data/15012890/standard.do)",
              "- [한국관광공사 TourAPI](https://www.data.go.kr/data/15101578/openapi.do)", "",
              "이번 작업은 수집·대조 파일 생성까지입니다. 서비스 DB 적재, RAG 인덱싱, 주기 실행은 아직 연결하지 않았습니다."]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def keys():
    result = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        value = m[2].strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0:
                raise ValueError("Multiline credentials are not supported")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        result[m[1]] = value
    return result


def get(url, params, headers=None):
    request = Request(url + "?" + urlencode(params), headers=headers or {})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(15_000_001)
        if len(raw) > 15_000_000:
            raise RuntimeError("Response too large")
        return json.loads(raw)
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from None
    except (ValueError, OSError):
        raise RuntimeError("Network error or non-JSON response (URL and key withheld)") from None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--sources", nargs="+", choices=["PARK", "TOUR"], default=["PARK", "TOUR"])
    parser.add_argument("--compare-kakao", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="Resume selected sources into an existing pilot directory")
    parser.add_argument("--comparison-only", action="store_true", help="Use the complete saved SBIZ snapshot")
    parser.add_argument("--rebuild-report", action="store_true", help="Reclassify saved data and rebuild places/report without API calls")
    args = parser.parse_args()
    if args.compare_kakao or args.comparison_only:
        parser.error("SBIZ comparison was retired with the collected data")
    if args.output_dir and (args.output_dir / "sbiz.json").exists():
        parser.error("Remove retired SBIZ data before resuming a pilot directory")
    if args.rebuild_report:
        if not args.output_dir:
            parser.error("--rebuild-report requires --output-dir")
        summary = json.loads((args.output_dir/"summary.json").read_text(encoding="utf-8"))
        write_report(args.output_dir, summary)
        print("OUTPUT", args.output_dir, flush=True)
        return
    credentials = keys()
    lat, lng = 37.5161987797456, 127.075940589715
    if not args.probe:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = args.output_dir or ROOT / "data" / "preprocessed" / "stadium_pilot" / ("JAMSIL_"+stamp)
        out.mkdir(parents=True,exist_ok=True)
        summary = {"stadium":"JAMSIL","center":{"lat":lat,"lng":lng},"radius_m":2500,
                   "distance_type":"straight_line","collected_at":stamp,"sources":{},
                   "note":"Counts are source records, not cross-provider deduplicated places"}
        if (out/"summary.json").exists():
            summary = json.loads((out/"summary.json").read_text(encoding="utf-8"))
        collected = {}
        if args.comparison_only:
            collected["SBIZ"] = json.loads((out/"sbiz.json").read_text(encoding="utf-8"))
        for source in ([] if args.comparison_only else args.sources):
            try:
                key = credentials.get(source+"_API_KEY","")
                if not key:
                    raise RuntimeError("Missing key")
                fn = {"SBIZ":collect_sbiz,"PARK":collect_parks,"TOUR":collect_tour}[source]
                rows,meta = fn(key,(lat,lng))
                collected[source] = rows
                (out/(source.lower()+".json")).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
                summary["sources"][source] = {"status":"ok","records":len(rows),"categories":dict(Counter(r["kind"] for r in rows)),**meta}
            except RuntimeError as e:
                summary["sources"][source] = {"status":"error","error":str(e)}
            print(source,json.dumps(summary["sources"][source],ensure_ascii=False),flush=True)
        if args.compare_kakao:
            if "SBIZ" not in collected:
                summary["comparison"] = {"status":"blocked","reason":"No successful SBIZ snapshot"}
            else:
                try:
                    key = credentials.get("KAKAO_REST_API_KEY","")
                    if not key:
                        raise RuntimeError("Missing Kakao key")
                    comparison = compare_kakao(key,collected["SBIZ"],(lat,lng))
                    (out/"kakao_comparison.json").write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding="utf-8")
                    summary["comparison"] = {"status":"ok","sample_size":comparison["sample_size"],"counts":comparison["counts"]}
                except RuntimeError as e:
                    summary["comparison"] = {"status":"error","error":str(e)}
        (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        write_report(out,summary)
        print("OUTPUT",out,flush=True)
        return
    probes = [
        ("PARK", PARK, "PARK_API_KEY", {"pageNo": 0, "numOfRows": 1, "type": "json"}),
        ("TOUR", TOUR, "TOUR_API_KEY", {"mapX": lng, "mapY": lat, "radius": 2500, "pageNo": 1, "numOfRows": 1, "MobileOS": "ETC", "MobileApp": "KBORoute", "_type": "json"}),
    ]
    for source, url, key, params in probes:
        try:
            if not credentials.get(key):
                raise RuntimeError("Missing key")
            payload = get(url, {**params, "serviceKey": unquote(credentials[key])})
            display = json.dumps(payload, ensure_ascii=False)
            for secret in credentials.values():
                if len(secret) >= 8:
                    display = display.replace(secret, "[REDACTED]").replace(unquote(secret), "[REDACTED]")
            print(source, display[:5000], flush=True)
        except RuntimeError as e:
            print(source, str(e), flush=True)


if __name__ == "__main__":
    main()
