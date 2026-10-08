"""Collect registered stadium public POIs and Google-only lodging references."""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import collect_stadium_pilot as pilot
from google_lodging import collect_lodging, id_references

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("PARK", "TOUR_WALK", "GOOGLE")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def stadiums():
    with (ROOT / "data/preprocessed/stadium_coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
        return [{"code": r["stadium_code"], "name": r["stadium_name_ko"],
                 "lat": float(r["lat_y"]), "lng": float(r["lng_x"])} for r in csv.DictReader(stream)]


def parks_for_stadium(rows, center):
    places = []
    for r in rows:
        p = pilot.record("PARK", r.get("manageNo") or r.get("MANAGE_NO"),
                         r.get("parkNm") or r.get("PARK_NM"), "walk_candidate",
                         r.get("rdnmadr") or r.get("lnmadr") or r.get("RDNMADR"),
                         r.get("latitude") or r.get("LATITUDE"),
                         r.get("longitude") or r.get("LONGITUDE"), center, r)
        if p:
            p.update(walk_type="park", category_small=r.get("parkSe"),
                     location_role="representative_point", trail_geometry=None,
                     walking_access="unverified")
            places.append(p)
    places = list({p["source_id"]: p for p in places}.values())
    return places, {"national_api_rows": len(rows), "selected_records": len(places),
                    "note": "Representative points only; not trail shapes or confirmed entrances"}


def tour_walks(key, center):
    rows = []
    # Filter at the provider: never request contentTypeId=32 (lodging).
    for content_type in (12, 28):
        rows.extend(pilot.all_pages(pilot.TOUR, {
            "serviceKey": unquote(key), "MobileOS": "ETC", "MobileApp": "KBORoute",
            "_type": "json", "mapX": center[1], "mapY": center[0], "radius": 2500,
            "arrange": "E", "contentTypeId": content_type}, size=100))
    places = []
    for r in rows:
        if str(r.get("contenttypeid")) not in ("12", "28"):
            continue
        name = r.get("title", "")
        if not re.search("공원|산책|둘레길|숲길|수목원|걷기|생태길", name):
            continue
        p = pilot.record("TOUR", r.get("contentid"), name, "walk_candidate", r.get("addr1"),
                         r.get("mapy"), r.get("mapx"), center, r)
        if p:
            p.update(walk_type="trail_candidate" if re.search("산책|둘레길|숲길|걷기|생태길", name) else "park_or_garden",
                     category_large=r.get("cat1"), category_middle=r.get("cat2"),
                     category_small=r.get("cat3"), location_role="representative_point",
                     trail_geometry=None, walking_access="unverified")
            places.append(p)
    places = list({p["source_id"]: p for p in places}.values())
    return places, {"api_rows": len(rows), "selected_records": len(places),
                    "content_types_requested": [12, 28],
                    "note": "Title-matched walking candidates; no lodging, route geometry or completeness guarantee"}


def report(out, summary):
    save(out / "summary.json", summary)
    lines = ["# 구장 주변 장소 수집 결과", "", f"수집 시작(UTC): {summary['started_at']}", "",
             "등록 구장 좌표 기준 직선 2,500m. 공공데이터 장소 기록과 Google 숙박 ID는 별도로 저장합니다.", "",
             "| 구장 | 공원 | 관광공사 산책 후보 | Google 숙박 후보 ID |",
             "|---|---:|---:|---:|"]
    failures = []
    for code, stadium in summary["stadiums"].items():
        sources = stadium["sources"]
        counts = []
        for source, field in (("PARK", "selected_records"), ("TOUR_WALK", "selected_records"), ("GOOGLE", "within_radius_ids")):
            result = sources.get(source, {})
            counts.append(str(result.get(field, 0)) if result.get("status") == "ok" else "—")
        lines.append("| " + " | ".join([stadium["name"], *counts]) + " |")
        for source, result in sources.items():
            if result.get("status") == "error":
                failures.append(f"- {code}/{source}: {result['error']}")
    lines.extend(["", "## Google 숙박 후보 분류", "",
                  "| 구장 | hotel | motel | lodging(세부 미확인) | 기타·미지정 | 검색 제한 잔여 영역 |",
                  "|---|---:|---:|---:|---:|---:|"])
    for stadium in summary["stadiums"].values():
        google = stadium["sources"].get("GOOGLE", {})
        if google.get("status") != "ok":
            continue
        types = google.get("primary_type_counts", {})
        hotel, motel, broad = (types.get(k, 0) for k in ("hotel", "motel", "lodging"))
        other = google["within_radius_ids"] - hotel - motel - broad
        lines.append(f"| {stadium['name']} | {hotel} | {motel} | {broad} | {other} | {google['unresolved_search_cells']} |")
    lines.extend(["", "기타에는 게스트하우스·호스텔·inn·캠핑장 등의 유형과 대표 유형 미지정 등이 포함됩니다."
                  " Google에 기록된 유형이며 국내 인허가 업태나 숙소 품질의 인증이 아닙니다.", "",
                  "- 숙박: Google Places만 조회합니다. `google_lodging_ids.json`은 Place ID만 보존합니다.",
                  "- Google 상호·주소·좌표·유형·영업 상태는 메모리에서 확인하며 영구 JSON/RAG로 내보내지 않습니다.",
                  "- Google Nearby 검색의 20건 제한에 도달한 영역은 세분화합니다. `unresolved_search_cells`와 `coverage`를 확인하세요. 0이어도 실제 전 업소 수집을 보증하지 않습니다.",
                  "- 공원과 관광공사 산책 후보는 출처 간 중복을 병합하지 않았습니다. 실제 보행 경로·출입구·접근 가능 여부는 미확인입니다.",
                  "- 서비스 DB 적재, 임베딩, 주기 갱신 작업은 실행하지 않았습니다.", "",
                  "## 출처 및 Google 이용 방식", "",
                  "- [Google Places 정책](https://developers.google.com/maps/documentation/places/web-service/policies)",
                  "- [Google Nearby 검색](https://developers.google.com/maps/documentation/places/web-service/nearby-search)",
                  "- Google 상세정보는 `google_lodging.fetch_detail()`로 요청 시 조회합니다. 표시 시 Google Maps 출처를 제공해야 하며 일반 Places 결과를 카카오 지도 마커로 결합하지 않습니다.",
                  "", "## 실패", "", *(failures or ["기록된 실패 없음. 출처별 완료 여부는 summary.json을 확인하세요."])])
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stadiums", nargs="+", help="Registered codes; omitted means all")
    parser.add_argument("--sources", choices=SOURCES, nargs="+", default=list(SOURCES))
    parser.add_argument("--output-dir", type=Path, help="Resume: completed sources are skipped")
    parser.add_argument("--refresh", action="store_true", help="Requery selected completed sources in an existing snapshot")
    args = parser.parse_args()
    registered = stadiums()
    if args.stadiums:
        unknown = set(args.stadiums) - {s["code"] for s in registered}
        if unknown:
            parser.error("Unknown stadium code(s): " + ", ".join(sorted(unknown)))
        registered = [s for s in registered if s["code"] in args.stadiums]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_dir or ROOT / "backend/artifacts/stadium_collection" / stamp
    summary = {"schema_version": 2, "started_at": stamp, "radius_m": 2500,
               "distance_type": "straight_line", "lodging_source": "GOOGLE_PLACES",
               "stadiums": {}}
    if (out / "summary.json").exists():
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        if summary.get("schema_version") != 2 or summary.get("lodging_source") != "GOOGLE_PLACES":
            parser.error("Incompatible output directory")
    credentials = pilot.keys()
    national_parks = None
    print("OUTPUT " + str(out), flush=True)
    had_error = False
    for stadium in registered:
        code = stadium["code"]
        center = (stadium["lat"], stadium["lng"])
        old = summary["stadiums"].get(code)
        if old and any(old[k] != stadium[k] for k in ("lat", "lng")):
            parser.error("Stadium center changed; start a new snapshot")
        entry = summary["stadiums"].setdefault(code, {**stadium, "sources": {}})
        for source in args.sources:
            if not args.refresh and entry["sources"].get(source, {}).get("status") == "ok":
                print(f"SKIP {code} {source} already completed", flush=True)
                continue
            print(f"START {code} {source}", flush=True)
            started = datetime.now(timezone.utc).isoformat()
            try:
                key_name = {"GOOGLE": "GOOGLE_PLACES_API_KEY", "TOUR_WALK": "TOUR_API_KEY"}.get(source, source + "_API_KEY")
                key = credentials.get(key_name)
                if not key:
                    raise RuntimeError("Missing " + key_name)
                if source == "PARK":
                    if national_parks is None:
                        cache = out / "parks_national.json"
                        if cache.exists():
                            national_parks = json.loads(cache.read_text(encoding="utf-8"))
                        else:
                            national_parks = pilot.all_pages(pilot.PARK, {"serviceKey": unquote(key), "type": "json"})
                            save(cache, national_parks)
                    places, meta = parks_for_stadium(national_parks, center)
                    save(out / code / "park_candidates.json", places)
                elif source == "TOUR_WALK":
                    places, meta = tour_walks(key, center)
                    save(out / code / "tour_walk_candidates.json", places)
                else:
                    live, meta = collect_lodging(key, center, progress=lambda calls, ids:
                                               print(f"PROGRESS {code} GOOGLE queries={calls} observed_ids={ids}", flush=True))
                    save(out / code / "google_lodging_ids.json", id_references(live))
                    del live
                entry["sources"][source] = {"status": "ok", "started_at": started,
                                            "completed_at": datetime.now(timezone.utc).isoformat(), **meta}
                print(f"DONE {code} {source} " + json.dumps(meta, ensure_ascii=False), flush=True)
            except RuntimeError as error:
                had_error = True
                entry["sources"][source] = {"status": "error", "started_at": started, "error": str(error)}
                print(f"FAILED {code} {source}: {error}", flush=True)
            report(out, summary)
        # Public-data export explicitly excludes Google responses and all lodging.
        public = []
        for filename in ("park_candidates.json", "tour_walk_candidates.json"):
            path = out / code / filename
            if path.exists():
                public.extend(json.loads(path.read_text(encoding="utf-8")))
        save(out / code / "public_places.json", public)
    report(out, summary)
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
