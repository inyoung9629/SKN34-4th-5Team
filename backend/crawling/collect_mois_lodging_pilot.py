"""Collect MOIS lodging records around Jamsil; keep registration status and raw fields.

Requires pyproj. This pilot writes files only, without updating service DB or RAG.
"""
import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from collect_stadium_pilot import ROOT, distance, get, keys

ENDPOINT = "https://apis.data.go.kr/1741000/lodgings/info"
SOURCE_PAGE = "https://www.data.go.kr/data/15155124/openapi.do"
# Include both banks of the Han River; do not restrict collection to Songpa.
DISTRICTS = {"3030000": "성동구", "3040000": "광진구", "3220000": "강남구", "3230000": "송파구"}
FIELD_LABELS = {
    "MNG_NO": "관리번호", "OPN_ATMY_GRP_CD": "개방자치단체코드", "BPLC_NM": "사업장명",
    "BZSTAT_SE_NM": "업태구분명", "SNTTN_BZSTAT_NM": "위생업태명",
    "SALS_STTS_CD": "영업상태코드", "SALS_STTS_NM": "영업상태명",
    "DTL_SALS_STTS_CD": "상세영업상태코드", "DTL_SALS_STTS_NM": "상세영업상태명",
    "ROAD_NM_ADDR": "도로명주소", "LOTNO_ADDR": "지번주소", "ROAD_NM_ZIP": "도로명우편번호",
    "LCTN_ZIP": "소재지우편번호", "CRD_INFO_X": "원본 X좌표", "CRD_INFO_Y": "원본 Y좌표",
    "TELNO": "전화번호", "LCPMT_YMD": "인허가일자", "CLSBIZ_YMD": "폐업일자",
    "LAST_MDFCN_PNT": "최종수정시점", "DAT_UPDT_PNT": "데이터갱신시점", "DAT_UPDT_SE": "데이터갱신구분",
    "KSRM_CNT": "한실수", "WSRM_CNT": "양실수", "LCTN_AREA": "소재지면적",
    "BLDG_PSN_SE_NM": "건물소유구분명", "BLDG_GRND_FLR_CNT": "건물지상층수",
    "BLDG_UDGD_FLR_CNT": "건물지하층수", "USE_BGNG_GRND_FLR": "사용시작지상층",
    "USE_ED_GRND_FLR": "사용끝지상층", "USE_BGNG_UDGD_FLR": "사용시작지하층", "USE_ED_UDGD_FLR": "사용끝지하층",
    "ML_PRCTR_CNT": "남성종사자수", "FML_PRCTR_CNT": "여성종사자수", "MLT_UTZTN_BSNSSP_YN": "다중이용업소여부",
    "CNDNAL_PRMSN_BGNG_YMD": "조건부허가시작일자", "CNDNAL_PRMSN_END_YMD": "조건부허가종료일자",
    "CNDNAL_PRMSN_DCLR_RSN": "조건부허가신고사유",
}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_page(payload):
    response = payload.get("response", {})
    code = str(response.get("header", {}).get("resultCode", ""))
    if code not in {"0", "00", "0000"}:
        raise RuntimeError("MOIS rejected request (provider response withheld)")
    body = response.get("body", {})
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise RuntimeError("Unexpected MOIS response structure")
    return items, int(body["totalCount"])


def collect_district(key, district):
    rows, ids, total = [], set(), None
    for page in range(1, 1001):
        params = {"serviceKey": unquote(key), "pageNo": page, "numOfRows": 100,
                  "returnType": "JSON", "cond[OPN_ATMY_GRP_CD::EQ]": district}
        for attempt in range(3):
            try:
                payload = get(ENDPOINT, params)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        items, expected = extract_page(payload)
        if total is not None and total != expected:
            raise RuntimeError("MOIS count changed during pagination; rerun snapshot")
        total = expected
        if not items and len(rows) < total:
            raise RuntimeError("MOIS returned an incomplete page sequence")
        for row in items:
            if row.get("OPN_ATMY_GRP_CD") != district:
                raise RuntimeError("MOIS did not apply district filter")
            ident = row.get("MNG_NO")
            if not ident or ident in ids:
                raise RuntimeError("Missing or duplicate management number during pagination")
            ids.add(ident)
        rows.extend(items)
        if len(rows) == total:
            return rows, {"name": DISTRICTS[district], "total_count": total,
                          "rows_received": len(rows), "pages": page, "unique_management_numbers": len(ids)}
        if len(rows) > total:
            raise RuntimeError("MOIS returned more rows than its total count")
        time.sleep(.15)
    raise RuntimeError("MOIS pagination exceeded pilot limit")


def number(value):
    try:
        parsed = float(str(value).replace(",", "").strip())
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def nonempty(value):
    return value is not None and str(value).strip() != ""


def field_coverage(rows):
    return {field: {
        "nonempty": sum(nonempty(row.get(field)) for row in rows),
        "zero": sum(number(row.get(field)) == 0 for row in rows),
        "positive_numeric": sum((number(row.get(field)) or 0) > 0 for row in rows),
    } for field in sorted({field for row in rows for field in row})}


def normalize(rows, center, transform):
    within, unknown, outside = [], [], 0
    for raw in rows:
        x, y = number(raw.get("CRD_INFO_X")), number(raw.get("CRD_INFO_Y"))
        lat = lng = None
        if x is not None and y is not None and x != 0 and y != 0:
            try:
                lng, lat = transform.transform(x, y, errcheck=True)
            except Exception:
                pass
        # Coordinates should also be plausible for the queried Seoul districts.
        if lat is None or lng is None or not (37.3 < lat < 37.8 and 126.7 < lng < 127.3):
            unknown.append({"reason": "missing_or_invalid_coordinates", "source_fields": raw})
            continue
        meters = distance(center[0], center[1], lat, lng)
        if meters > 2500:
            outside += 1
            continue
        within.append({"source": "MOIS_LODGING", "source_id": raw["MNG_NO"],
                       "district": DISTRICTS[raw["OPN_ATMY_GRP_CD"]], "name": raw.get("BPLC_NM"),
                       "address": raw.get("ROAD_NM_ADDR") or raw.get("LOTNO_ADDR"),
                       "kind": "lodging", "source_category": raw.get("BZSTAT_SE_NM"),
                       "status": raw.get("SALS_STTS_NM"), "status_code": raw.get("SALS_STTS_CD"),
                       "detail_status": raw.get("DTL_SALS_STTS_NM"),
                       "lat": lat, "lng": lng, "distance_m": round(meters, 1), "source_fields": raw})
    return sorted(within, key=lambda r: (r["distance_m"], r["source_id"])), unknown, outside


def counts(rows, field):
    return dict(sorted(Counter(row.get(field) or "미기재" for row in rows).items()))


def analyze(out, transform):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    rows = [row for code in DISTRICTS for row in json.loads((out / f"raw_{code}.json").read_text(encoding="utf-8"))]
    center = (manifest["center"]["lat"], manifest["center"]["lng"])
    within, unknown, outside = normalize(rows, center, transform)
    active = [p for p in within if p["status_code"] == "01"]
    raw_active = [p["source_fields"] for p in active]
    summary = {**manifest, "district_records": len(rows), "within_radius_records": len(within),
               "within_radius_unique_management_numbers": len({p["source_id"] for p in within}),
               "active_records": len(active), "outside_radius_records": outside,
               "unlocated_records": len(unknown),
               "unlocated_status_counts": counts([p["source_fields"] for p in unknown], "SALS_STTS_NM"),
               "status_counts": counts(within, "status"), "detail_status_counts": counts(within, "detail_status"),
               "all_category_counts": counts(within, "source_category"),
               "active_category_counts": counts(active, "source_category"),
               "active_district_counts": counts(active, "district"),
               "active_positive_room_count_records": sum(
                   (number(r.get("KSRM_CNT")) or 0) + (number(r.get("WSRM_CNT")) or 0) > 0 for r in raw_active),
               "active_field_coverage": field_coverage(raw_active),
               "all_field_coverage": field_coverage([p["source_fields"] for p in within]),
               "note": "Counts are administrative registrations, not deduplicated physical premises or live bookable rooms"}
    save(out / "lodgings_all_statuses.json", within)
    save(out / "lodgings_active.json", active)
    save(out / "unlocated.json", unknown)
    if (out / "location_followup.json").exists():
        summary["location_followup"] = json.loads((out / "location_followup.json").read_text(encoding="utf-8"))
    save(out / "summary.json", summary)
    lines = ["# 잠실 반경 2.5km 행안부 숙박업 수집 결과", "",
             f"- 수집 시각(UTC): {manifest['collected_at']}",
             f"- 기준: 잠실야구장 {center[0]}, {center[1]} / 직선거리 2,500m 이내",
             f"- 조회: 성동·광진·강남·송파구 모든 영업 상태, 총 {len(rows)}건 / 모든 페이지 수신 확인",
             "- 좌표: EPSG:5174 → EPSG:4326, pyproj(always_xy=True)",
             f"- 반경 내 {len(within)}건, 영업/정상 {len(active)}건, 반경 밖 {outside}건",
             f"- 조회한 4개 구 전체에서 좌표 누락·이상 {len(unknown)}건은 원본만으로 반경 여부 미확정: unlocated.json",
             f"- 좌표 누락·이상 상태: {summary['unlocated_status_counts']} (반경 내 건수와 별개)",
             "- 행정상 영업 상태이며 실제 운영·예약 가능 여부를 보장하지 않습니다.",
             "- 인허가 기록 수이며 같은 건물의 복수 등록·폐업 후 재등록을 임의로 병합하지 않았습니다.",
             "- 이 결과는 숙박업 조회서비스에 한정됩니다. 도시민박 등 별도 인허가 자료는 포함하지 않습니다.",
             "- 공식 안내: 매일 갱신, 2일 전 기준 현행화. 각 업소의 최종 수정일은 그보다 오래될 수 있습니다.",
             f"- [공식 데이터 및 명세]({SOURCE_PAGE})", "", "## 영업 상태", "",
             "| 상태 | 건수 |", "|---|---:|"]
    lines.extend(f"| {status} | {n} |" for status, n in summary["status_counts"].items())
    lines += ["", "## 업태 분류", "", "| 원본 업태 | 영업/정상 | 전체 상태 |", "|---|---:|---:|"]
    lines.extend(f"| {cat} | {summary['active_category_counts'].get(cat, 0)} | {n} |"
                 for cat, n in summary["all_category_counts"].items())
    lines += ["", "분류는 원본 행정 업태입니다. 상호나 여행 서비스의 숙소 유형과 일대일 대응하지 않습니다.",
              "예: 상주호텔은 여관업, 스텔라 호스텔은 관광호텔, 호스텔 메가 잠실새내역은 숙박업(생활)로 응답했습니다."]
    if summary.get("location_followup"):
        lines += ["", "## 원본 좌표가 없는 영업 업소의 주소 보완", "",
                  "소상공인 공공데이터의 동일 도로명 건물 주소를 대조했습니다. 다른 업소와 같은 건물인 경우도 있으므로",
                  f"숙박업소 자체의 좌표·사업체 동일성을 확정한 결과가 아닙니다. 위 {len(active)}건 집계에는 추가하지 않았습니다.", "",
                  "| 행안부 상호 | 주소 좌표 참고 결과 | 근거 |", "|---|---|---|"]
        for followup in summary["location_followup"]["records"]:
            candidates = followup["candidates"]
            distances = ", ".join(str(c["distance_m"]) + "m" for c in candidates) or "미확인"
            evidence = ", ".join(c["name"] + " / " + c["match_type"] for c in candidates) or "대조 자료 없음"
            lines.append(f"| {followup['mois_name']} | {distances} | {evidence} |")
    lines += ["", "## 항목 충실도 — 영업/정상 기준", "",
              "빈 값은 미확인입니다. 0이 입력된 객실 수·시설 수를 실제 없음으로 단정하지 않습니다.", "",
              f"한실+양실 수 합계가 양수인 기록: {summary['active_positive_room_count_records']}/{len(active)}. 실시간 잔여 객실이 아닙니다.",
              "가격, 예약 가능 객실, 별 등급, 체크인/체크아웃, 조식, 주차, 취사·반려동물 조건, 사진은 이 응답에 없습니다.", "",
              "| 항목 | 원본 필드 | 값 있음 | 숫자 0 | 양수 |", "|---|---|---:|---:|---:|"]
    lines.extend(f"| {FIELD_LABELS.get(key, '')} | {key} | {value['nonempty']}/{len(active)} | {value['zero']} | {value['positive_numeric']} |"
                 for key, value in summary["active_field_coverage"].items())
    lines += ["", "## 영업/정상 업소 목록", "", "| 상호 | 원본 업태 | 거리(m) | 주소 |", "|---|---|---:|---|"]
    for p in active:
        cells = [str(p[key] or "").replace("|", "\\|").replace("\n", " ")
                 for key in ("name", "source_category", "distance_m", "address")]
        lines.append("| " + " | ".join(cells) + " |")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dependency-dir", type=Path, help="Optional directory containing pyproj")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--rebuild", action="store_true", help="Analyze cached raw records without API requests")
    args = parser.parse_args()
    if args.rebuild and not args.output_dir:
        parser.error("--rebuild requires --output-dir")
    if args.dependency_dir:
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    from pyproj import Transformer, __version__ as pyproj_version
    transform = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True, allow_ballpark=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_dir or ROOT / "data/preprocessed/stadium_pilot" / ("JAMSIL_MOIS_" + stamp)
    if not args.rebuild:
        if out.exists():
            raise RuntimeError("Choose a new output directory to preserve the previous snapshot")
        credentials = keys()
        # User confirmed the common portal key and approved this lodging service.
        key = credentials.get("MOIS_LODGING_API_KEY") or credentials.get("SBIZ_API_KEY")
        if not key:
            raise RuntimeError("Missing local public-data credential")
        with (ROOT / "data/preprocessed/stadium_coordinates.csv").open(encoding="utf-8-sig", newline="") as f:
            stadium = next(row for row in csv.DictReader(f) if row["stadium_code"] == "JAMSIL")
        manifest = {"source": "MOIS_LODGING", "source_page": SOURCE_PAGE, "endpoint": ENDPOINT,
                    "stadium": "JAMSIL", "radius_m": 2500, "distance_type": "straight_line",
                    "center": {"lat": float(stadium["lat_y"]), "lng": float(stadium["lng_x"])},
                    "collected_at": stamp, "pyproj_version": pyproj_version, "districts": {}}
        out.mkdir(parents=True)
        for code, name in DISTRICTS.items():
            rows, meta = collect_district(key, code)
            save(out / f"raw_{code}.json", rows)
            manifest["districts"][code] = meta
            print(name, json.dumps(meta, ensure_ascii=False), flush=True)
        save(out / "manifest.json", manifest)
    summary = analyze(out, transform)
    print(json.dumps({k: summary[k] for k in ["district_records", "within_radius_records", "active_records",
                                            "status_counts", "active_category_counts", "unlocated_records"]}, ensure_ascii=False))
    print("OUTPUT", out, flush=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("Collection failed:", str(error), file=sys.stderr)
        sys.exit(1)
