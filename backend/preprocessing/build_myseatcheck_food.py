"""Build food facts from browser-observed headings/links, never guessed store URLs.

Input is a completed, local census with rootLinks/pages/queue/errors. No network
access or copied article/photo bodies. Existing IDs survive matching source rows.
"""
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote


def url_key(url):
    return unquote(url).split("?")[0].split("#")[0].rstrip("/") + "/"


def name_key(name):
    return re.sub(r"[^가-힣a-z0-9]", "", name.lower())


def title_parts(title):
    # Suwon uses 'name - location'; other parks use 'name (location)'.
    if " - " in title:
        return tuple(title.split(" - ", 1))
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)\s*", title)
    return (match[1].strip(), match[2].strip()) if match else (title, "")


def category(name):
    # Reviewed cafe/dessert names in this census. Mixed food/convenience counters
    # remain FOOD; standalone convenience stores retain their course subtype.
    if re.fullmatch(r"(?:GS25|세븐일레븐\d*|이마트24|CU\s*(?:편의점)?|편의점|홈런마트|T\s*mart)", name, re.I):
        return "CONVENIENCE"
    if re.search(r"카페|커피|coffee|cafe|스타벅스|이디야|투썸|공차|탐앤탐스|폴바셋|파스쿠찌|빽다방|베이커|베이크|도넛|츄러스|프레즐|아이스크림|요[아야]정|빙수|젤라또|밀락당|프루토프루타|빙동댕|pick\s*me\s*31|버터우드|소프텔리에|디저트|크레페|설빙|땅콩빵|소르베|코아양과|요거트월드|커빙|KOPI BALI|라온스위트파크", name, re.I):
        return "CAFE"
    return "FOOD"


def build(census, previous):
    pages = {url_key(p["url"]): p for p in census["pages"]}
    aliases = {url_key(p["from"]): url_key(p["to"]) for p in census.get("redirects", [])}
    if census.get("errors") or any(url_key(p["url"]) not in pages for p in census["queue"]):
        raise ValueError("Complete the source census before publishing")
    roots = {p["stadium"]: url_key(p["url"]) for p in census["rootLinks"]}
    if len(roots) != 9:
        raise ValueError("Expected all nine supported KBO stadiums")
    for page in pages.values():
        for heading in page["headings"]:
            href = heading.get("href")
            if href and "먹거리" in unquote(href) and not re.search(r"전체|보러|클릭|돌아", heading["text"]):
                target = url_key(href)
                if aliases.get(target, target) not in pages:
                    raise ValueError(f"Unvisited food-list link: {heading['text']}")
    old_by_code = {code: [r for r in previous if r["stadium_code"] == code] for code in roots}
    next_ids = {code: max(int(r["record_id"].rsplit("_", 1)[1]) for r in rows) for code, rows in old_by_code.items()}
    used, records, exceptions = set(), [], []
    for key, page in pages.items():
        heads = page["headings"]
        if not any("사진 클릭" in h["text"] for h in heads):
            continue
        code, title = page["stadium"], heads[0]["text"]
        name, location = title_parts(title)
        # Photos show food counters, but the title only names the area. Keep the
        # original label and link, without inventing an individual brand.
        area_entry = name in ("4층 스플래시존", "파티플로어석")
        if area_entry:
            exceptions.append({"stadium": code, "title": title, "url": key, "reason": "먹거리 구역명으로 포함 · 개별 매장명 미표기"})
        parent = pages.get(url_key(page.get("parent", "")))
        parent_url = url_key(parent["url"]) if parent else roots[code]
        zone = re.sub(r"^먹거리\s*", "", parent["headings"][0]["text"]) if parent and parent.get("depth") != 0 else location
        candidates = [r for r in old_by_code[code] if r["record_id"] not in used]
        # Prefer the listing link + observed heading name over a repeated brand.
        labels = {name_key(name), name_key(page.get("label", ""))}
        exact = [r for r in candidates if name_key(r["store_facility"]) in labels and url_key(r["source_url"]) in (parent_url, key)]
        if not exact:
            exact = [r for r in candidates if name_key(r["store_facility"]) in labels and (r["zone_location"] == zone or r["zone_location"] == location)]
        if not exact:
            same_name = [r for r in candidates if name_key(r["store_facility"]) in labels]
            exact = same_name if len(same_name) == 1 else []
        if exact:
            base = dict(exact[0])
        else:
            base = {k: "" for k in previous[0]}
            base.update({k: old_by_code[code][0][k] for k in ("stadium_code", "stadium_name", "home_team")})
            next_ids[code] += 1
            base["record_id"] = f"SC_FOOD_{code}_{next_ids[code]:03d}"
        used.add(base["record_id"])
        floor = re.search(r"(?:지하\s*)?\d+(?:\.\d+)?층", location) or re.search(r"(?:지하\s*)?\d+(?:\.\d+)?층", zone)
        floor = floor[0] if floor else "외부" if "외부" in location + zone else base.get("floor", "")
        exterior = "외부" in location + zone or (base.get("in_stadium_flag") == "N" and not location)
        base.update(store_facility=name, zone_location=zone, floor=floor,
                    in_stadium_flag="N" if exterior else "Y", evidence_type="UNOFFICIAL",
                    evidence_subtype="COMMUNITY_SEATVIEW", source="myseatcheck.com (자리어때)",
                    source_url=key, verified_at=census["checkedAt"][:10], status="DETAIL_CHECKED",
                    source_title=title, source_location=location or zone, listing_url=parent_url,
                    food_category=category(name), source_entry_type="area" if area_entry else "store",
                    notes="외부 표기 포함 내부 먹거리 분류. 표시 핀은 실제 매장 좌표 아님. 현재 영업·메뉴 미확인")
        base["content"] = f"[{base['stadium_name']}] {name} / {base['source_location']} / {zone} / {base['food_category']} / 매장 상세 위치: {key}"
        records.append(base)
    records.sort(key=lambda r: r["record_id"])
    report = {"checkedAt": census["checkedAt"], "pageCount": len(pages), "storeCount": len(records),
              "counts": {code: dict(Counter(r["food_category"] for r in records if r["stadium_code"] == code)) for code in roots},
              "sourceExteriorCount": sum(r["in_stadium_flag"] == "N" for r in records),
              "rootUrls": roots, "areaOnlyPages": exceptions,
              "redirects": aliases,
              "correctedLinks": [{"stadium": p["stadium"], "name": p["label"], "detailUrl": url_key(p["url"]), "note": p["discovery"]}
                                 for p in pages.values() if p.get("discovery")],
              "retiredRecords": [{"id": r["record_id"], "name": r["store_facility"], "zone": r["zone_location"]} for r in previous if r["record_id"] not in used]}
    return records, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("census", type=Path)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    census = json.loads(args.census.read_text(encoding="utf-8"))
    with args.previous.open(encoding="utf-8-sig", newline="") as stream:
        previous = list(csv.DictReader(stream))
    records, report = build(census, previous)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    args.audit.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
