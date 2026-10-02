"""Save Kakao lodging IDs/URLs, never the search response or venue details.

Run from the repository root (no database, Django, LLM or detail-page crawling):
    .venv/Scripts/python.exe backend/crawling/collect_kakao_lodging_references.py
    .venv/Scripts/python.exe backend/crawling/collect_kakao_lodging_references.py --execute

The first command is a dry run. Each execution creates a new ignored snapshot.
The five queries mirror the current lodging filters, not an exhaustive census.
Stadium files record this run's search grouping, not enduring geographic facts.
References are deliberately separate from the factual/keyword RAG index.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
STADIUM_CODES = (
    "JAMSIL", "GOCHEOK", "MUNHAK", "SUWON", "DAEJEON", "DAEGU",
    "GWANGJU", "SAJIK", "CHANGWON",
)
KEYWORDS = ("", "호텔", "모텔", "여관", "여인숙")
RADIUS_M = 2500
PAGE_SIZE = 15
MAX_PAGES = 3
MAX_REQUESTS = len(STADIUM_CODES) * len(KEYWORDS) * MAX_PAGES
MAX_RESPONSE_BYTES = 262144
MIN_INTERVAL_SECONDS = 0.5
ENDPOINT = "https://dapi.kakao.com/v2/local/search/"


class CollectionError(RuntimeError):
    """Only fixed, credential-free error codes may cross the CLI boundary."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_key():
    """Match backend .env precedence without printing or retaining other keys."""
    name = "KAKAO_REST_API_KEY"
    value = os.environ.get(name)
    if value:
        return value
    for path in (ROOT / "backend/.env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?KAKAO_REST_API_KEY\s*=\s*(.*)$", line)
            if not match:
                continue
            value = match[1].strip()
            if value[:1] in ("'", '"'):
                end = value.find(value[0], 1)
                if end < 0:
                    raise CollectionError("INVALID_KEY_CONFIGURATION")
                value = value[1:end]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if value:
                return value
    raise CollectionError("MISSING_KAKAO_REST_API_KEY")


def stadium_centers():
    data = json.loads((ROOT / "data/preprocessed/stadium_locations.json").read_text(encoding="utf-8"))
    centers = {}
    for code in STADIUM_CODES:
        point = data["stadiums"][code]
        lat, lng = point["lat"], point["lng"]
        if not (point["south"] < lat < point["north"] and point["west"] < lng < point["east"]):
            raise CollectionError("INVALID_REVIEWED_STADIUM_CENTER")
        centers[code] = {"lat": lat, "lng": lng}
    return centers


class KakaoClient:
    def __init__(self, key):
        self._key = key
        self._opener = build_opener(NoRedirect())
        self._last_request = None
        self.requests_attempted = 0

    def __call__(self, center, keyword, page):
        if keyword not in KEYWORDS or not 1 <= page <= MAX_PAGES:
            raise CollectionError("INVALID_QUERY_CONFIGURATION")
        if self.requests_attempted >= MAX_REQUESTS:
            raise CollectionError("REQUEST_BUDGET_EXHAUSTED")
        if self._last_request is not None:
            time.sleep(max(0, MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request)))
        params = {"category_group_code": "AD5", "x": center["lng"], "y": center["lat"],
                  "radius": RADIUS_M, "size": PAGE_SIZE, "page": page, "sort": "distance"}
        if keyword:
            params["query"] = keyword
        url = ENDPOINT + ("keyword.json" if keyword else "category.json") + "?" + urlencode(params)
        request = Request(url, headers={"Authorization": "KakaoAK " + self._key,
                                        "Accept": "application/json"})
        self.requests_attempted += 1
        self._last_request = time.monotonic()
        try:
            with self._opener.open(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CollectionError("RESPONSE_TOO_LARGE")
            return json.loads(raw)
        except HTTPError as error:
            # No retries or alternate hosts after rate/access restrictions.
            raise CollectionError(f"KAKAO_HTTP_{error.code}") from None
        except (URLError, OSError):
            raise CollectionError("KAKAO_NETWORK_ERROR") from None
        except (ValueError, UnicodeError):
            raise CollectionError("KAKAO_INVALID_JSON") from None


def canonical_reference(place_id, url):
    if not isinstance(place_id, str) or not re.fullmatch(r"[0-9]{1,100}", place_id):
        raise CollectionError("INVALID_PLACE_ID")
    if not isinstance(url, str):
        raise CollectionError("INVALID_PLACE_URL")
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in ("http", "https") and parts.netloc == "place.map.kakao.com"
                 and parts.path == "/" + place_id and not parts.query and not parts.fragment
                 and not any(char.isspace() for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise CollectionError("INVALID_PLACE_URL")
    return {"place_id": place_id, "place_url": "https://place.map.kakao.com/" + place_id}


def distance_m(center, lat, lng):
    lat1, lat2 = math.radians(center["lat"]), math.radians(lat)
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(lng - center["lng"]) / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, a))))


def matches_filter(category, keyword):
    """Mirror lodgingSubtype/filter matching; classification stays in memory."""
    if not keyword:
        return True
    if not isinstance(category, str):
        raise CollectionError("INVALID_PLACE_CATEGORY")
    matched = {name for pattern, name in (("호텔", "hotel"), ("모텔", "motel"), ("여관|여인숙", "inn"))
               if re.search(pattern, category)}
    expected = "hotel" if keyword == "호텔" else "motel" if keyword == "모텔" else "inn"
    return matched == {expected} or (expected in {"motel", "inn"} and matched == {"motel", "inn"})


def page_references(payload, center, keyword):
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
        raise CollectionError("INVALID_PAGE_RESPONSE")
    is_end = payload["meta"].get("is_end")
    documents = payload.get("documents")
    if type(is_end) is not bool or not isinstance(documents, list) or len(documents) > PAGE_SIZE:
        raise CollectionError("INVALID_PAGE_RESPONSE")
    if not documents and not is_end:
        raise CollectionError("INVALID_PAGE_RESPONSE")
    refs = []
    for row in documents:
        if not isinstance(row, dict) or row.get("category_group_code") != "AD5":
            raise CollectionError("NON_LODGING_RESPONSE")
        reference = canonical_reference(row.get("id"), row.get("place_url"))
        try:
            lat, lng = float(row["y"]), float(row["x"])
        except (KeyError, TypeError, ValueError):
            raise CollectionError("INVALID_PLACE_LOCATION") from None
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            raise CollectionError("INVALID_PLACE_LOCATION")
        if distance_m(center, lat, lng) <= RADIUS_M and matches_filter(row.get("category_name", ""), keyword):
            refs.append(reference)
    return refs, is_end


def collect_stadium(center, search):
    found = {}
    capped = False
    for keyword in KEYWORDS:
        for page in range(1, MAX_PAGES + 1):
            # Project down immediately; never retain or serialize the raw page.
            refs, is_end = page_references(search(center, keyword, page), center, keyword)
            found.update((row["place_id"], row) for row in refs)
            if is_end:
                break
            if page == MAX_PAGES:
                capped = True
    return list(found.values()), capped


def write_references(path, rows):
    """Whitelist again at the persistence boundary, even for internal callers."""
    unique = {}
    for row in rows:
        ref = canonical_reference(row.get("place_id"), row.get("place_url"))
        unique[ref["place_id"]] = ref
    ordered = sorted(unique.values(), key=lambda row: (len(row["place_id"]), row["place_id"]))
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return ordered


def save_snapshot(output, centers, client, progress=print):
    # Refuse reuse/overwriting a previous run, including a failed snapshot.
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1, "source": "KAKAO", "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "request_configuration": {"stadium_centers": centers, "radius_m": RADIUS_M,
                                  "category": "AD5", "keywords": list(KEYWORDS),
                                  "page_size": PAGE_SIZE, "max_pages_per_query": MAX_PAGES,
                                  "max_requests": MAX_REQUESTS},
        "reference_fields": ["place_id", "place_url"],
        "scope": "bounded_search_references_not_exhaustive_not_factual_rag",
        "stadiums": {},
    }
    all_refs = {}
    try:
        for code, center in centers.items():
            if code not in STADIUM_CODES:
                raise CollectionError("INVALID_STADIUM_CODE")
            refs, capped = collect_stadium(center, client)
            refs = write_references(output / (code + ".jsonl"), refs)
            all_refs.update((row["place_id"], row) for row in refs)
            manifest["stadiums"][code] = {"file": code + ".jsonl", "saved_reference_count": len(refs)}
            # Provider cap signals are only displayed, not retained as venue data.
            progress(f"{code}: saved={len(refs)} api_requests={client.requests_attempted}"
                     + (" (page cap reached; not exhaustive)" if capped else ""))
        manifest["status"] = "complete"
    except CollectionError as error:
        manifest["status"] = "failed"
        manifest["error_code"] = str(error)
        raise
    except (OSError, KeyboardInterrupt):
        manifest["status"] = "interrupted"
        raise
    finally:
        rows = write_references(output / "all.jsonl", all_refs.values())
        with (output / "urls.txt").open("x", encoding="utf-8", newline="\n") as handle:
            handle.writelines(row["place_url"] + "\n" for row in rows)
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["api_requests_attempted"] = client.requests_attempted
        manifest["unique_reference_count"] = len(rows)
        manifest["files_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir()) if path.suffix in {".jsonl", ".txt"}
        }
        with (output / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Perform bounded live Kakao API searches")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    centers = stadium_centers()
    print(f"stadiums={len(centers)} radius_m={RADIUS_M} max_api_requests={MAX_REQUESTS}", flush=True)
    if not args.execute:
        print("Dry run: no API requests or files. Add --execute to save IDs and URLs only.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "backend/artifacts/kakao-lodging-references" / stamp
    try:
        client = KakaoClient(load_key())
        print(f"OUTPUT {output}", flush=True)
        manifest = save_snapshot(output, centers, client, progress=lambda line: print(line, flush=True))
    except CollectionError as error:
        print(f"STOPPED {error}; no automatic retries or detail-page access", flush=True)
        return 1
    except (OSError, KeyboardInterrupt):
        print("STOPPED LOCAL_IO_OR_INTERRUPT; completed stadium files are preserved", flush=True)
        return 1
    print(f"COMPLETE unique_urls={manifest['unique_reference_count']} api_requests={client.requests_attempted}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
