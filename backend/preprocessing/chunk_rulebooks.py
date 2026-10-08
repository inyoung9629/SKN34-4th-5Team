"""KBO 규정집 PDF → 조·항 단위 RULE 청크 CSV (4차 #14).

입력: data/raw/rules/2026_KBO야구규칙.pdf, 2026_KBO리그규정.pdf
출력: data/preprocessed/kbo_rulebook_chunks.csv  (1행 = 1청크)

청킹 원칙 (2026-09-23 결정)
- 기본 단위는 "조" (야구규칙 N.NN / 리그규정 제N조 / 부록의 번호 항목).
- 한 조가 MAX_CHARS를 넘으면 하위 항(⒜⒝… / ①②… / 1. 2. …) 경계에서 나누고,
  나눈 조각마다 문서명·장·조 제목을 헤더로 반복해 각 청크가 자기완결적이도록 한다.
- 원문 문장은 손대지 않는다(요약·재작성 없음). 페이지 러닝헤더·푸터만 제거.
- 야구규약은 구단·선수 계약 위주라 수집 대상에서 제외(원본도 raw에 두지 않음). 필요해지면 PDF 받아 SOURCES에 한 줄 추가.

실행:
    pip install pymupdf        # requirements.txt에 없음 (전처리 전용, 백엔드 이미지에 넣지 않음)
    python backend/preprocessing/chunk_rulebooks.py
    python backend/preprocessing/chunk_rulebooks.py --raw data/raw/rules --out data/preprocessed/kbo_rulebook_chunks.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # PyMuPDF

# 분할 상수. rag_test/exp_chunking.py 실험으로 결정 (2026-09-23) — 두 문서 공통값이다.
# 문서별로 다른 값을 줘 봤지만(야구규칙 1600 / 리그규정 1200) 31문항 순위가 하나도 달라지지 않았고,
# 공통 1400이 같은 MRR을 문맥 5% 적게 쓰고 낸다. 문서별 차이는 파서 두 개가 이미 흡수한다.
MAX_CHARS = 1400      # 이 길이를 넘는 조는 항 단위로 분할
MIN_MERGE = 250       # 이보다 짧은 조각은 인접 조각과 합침 (조 안에서, 리그규정 부록은 같은 소절 안에서)
HARD_MAX = 2100       # 하위 마커가 없어도 이 길이를 넘으면 문장 경계에서 강제 분할

# 하위 항 마커 (레벨 순서). 줄 시작 기준.
SUB_LEVELS = [
    r"^[⒜-⒵]",            # ⒜ ⒝ … (야구규칙)
    r"^\(?[a-z]\)\s?|^[a-z]\.\s",  # (a) / a.  (리그규정 부록)
    r"^\d{1,2}\.\s",        # 1. 2. … (리그규정 항)
    r"^[①-⑳]",             # ① ② …
    r"^[⑴-⒇]",             # ⑴ ⑵ …
    r"^[가-힣]\.\s",        # 가. 나. …
    r"^\[(주|부기|원주|벌칙|예|주\d|부기\d)\]",  # 주석
]
SUB_RE = [re.compile(p) for p in SUB_LEVELS]
ANY_MARKER = re.compile("|".join(f"(?:{p})" for p in SUB_LEVELS))


@dataclass
class Chunk:
    source: str          # BASEBALL_RULES | LEAGUE_REGULATIONS
    source_file: str
    section: str         # 장/부록 이름
    article_no: str      # 5.06 / 제44조 / 32 / APP-피치클락-2
    article_title: str
    body: str
    page_start: int
    page_end: int
    part: int = 1
    parts: int = 1
    kind: str = "ARTICLE"   # ARTICLE | GLOSSARY | APPENDIX | PREAMBLE | CHANGELOG | TABLE


# --------------------------------------------------------------------------- 공통

def page_lines(pdf: Path) -> list[tuple[int, list[str]]]:
    doc = pymupdf.open(pdf)
    out = []
    for i, p in enumerate(doc):
        out.append((i + 1, p.get_text().split("\n")))
    return out


FULL_LINE = 28   # 본문 한 줄 폭이 33~38자. 이보다 짧은 줄은 표 셀·목록 끝으로 보고 공백으로 잇는다


KO_ITEM = re.compile(r"^[가-하]\.\s")


def _starts_item(s: str, last: str) -> bool:
    """줄 s가 새 항목(마커)으로 시작하는가. '다. 단, …'처럼 문장이 줄 끝에서 '…된' / '다.'로
    끊긴 뒤 이어지는 줄은 가./나./다. 목록 마커로 오인하지 않는다."""
    if not ANY_MARKER.match(s):
        return False
    if KO_ITEM.match(s):
        prev = last.rstrip()
        full_wrap = len(prev) >= FULL_LINE and not last.endswith(" ") and not prev.endswith(("다.", ")", "）", "、", ","))
        if full_wrap:
            return False
    return True


def join_lines(lines: list[str]) -> str:
    """PDF 하드 줄바꿈 복원.
    - 마커(⒜ ① 1. [주] …)로 시작하는 줄 앞에서만 줄을 바꾼다.
    - 앞 줄이 문장 끝(다. / : )이면 줄바꿈 유지.
    - 앞 줄이 꽉 찬 줄(≥FULL_LINE)이면 음절 중간에서 끊긴 것으로 보고 그대로 붙인다
      (원문에 공백이 있으면 pymupdf가 trailing space로 남겨 준다).
    - 앞 줄이 짧으면(표 셀, 목록 항목 끝) 공백 하나로 잇는다.
    """
    buf: list[str] = []
    last: str = ""          # 직전 "물리적" 줄 (버퍼 누적분이 아니라 PDF의 한 줄)
    for raw in lines:
        if not raw.strip():
            continue
        s = raw.strip()
        line = raw.rstrip("\n").lstrip()
        if not buf or _starts_item(s, last) or last.rstrip().endswith(("다.", "다)", ":", "：")):
            buf.append(line)
        elif last.endswith(" ") or len(last.rstrip()) < FULL_LINE or last.rstrip().endswith((",", "、", ")", "）")):
            buf[-1] = buf[-1].rstrip() + " " + s
        else:
            buf[-1] = buf[-1] + s
        last = line
    text = "\n".join(b.strip() for b in buf)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def split_by_level(text: str, level: int) -> list[str]:
    """level 번째 마커 기준으로 텍스트를 블록으로 나눈다. 마커 앞 부분(서두)은 첫 블록."""
    if level >= len(SUB_RE):
        return [text]
    rx = SUB_RE[level]
    lines = text.split("\n")
    blocks: list[list[str]] = [[]]
    for ln in lines:
        if rx.match(ln) and blocks[-1]:
            blocks.append([ln])
        else:
            blocks[-1].append(ln)
    return ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]


def split_sentences(text: str, limit: int) -> list[str]:
    parts, cur = [], ""
    for sent in re.split(r"(?<=다\.)\s|(?<=\.)\n", text):
        if len(cur) + len(sent) > limit and cur:
            parts.append(cur.strip()); cur = sent
        else:
            cur = (cur + " " + sent) if cur else sent
    if cur.strip():
        parts.append(cur.strip())
    return parts


def pack(blocks: list[str], limit: int) -> list[str]:
    """블록을 순서대로 limit 이하로 묶는다."""
    out, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 1 > limit:
            out.append(cur); cur = b
        else:
            cur = (cur + "\n" + b) if cur else b
    if cur:
        out.append(cur)
    return out


def split_article(text: str, level: int = 0) -> list[str]:
    """조 본문을 MAX_CHARS 이하 조각으로. 하위 마커 레벨을 차례로 시도."""
    if len(text) <= MAX_CHARS:
        return [text]
    for lv in range(level, len(SUB_RE)):
        blocks = split_by_level(text, lv)
        if len(blocks) <= 1:
            continue
        pieces: list[str] = []
        for b in blocks:
            if len(b) > MAX_CHARS:
                pieces.extend(split_article(b, lv + 1))
            else:
                pieces.append(b)
        return pack(pieces, MAX_CHARS)
    if len(text) > HARD_MAX:
        return split_sentences(text, MAX_CHARS)
    return [text]


def emit(chunks: list[Chunk], base: Chunk) -> None:
    pieces = split_article(base.body)
    # 짧은 조각(서두 한 줄, 꼬리 한 줄)은 이웃 조각에 합쳐 홀로 남기지 않는다
    merged: list[str] = list(pieces)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, p in enumerate(merged):
            if len(p) >= MIN_MERGE:
                continue
            cand = []
            if i > 0:
                cand.append((len(merged[i - 1]), i - 1))
            if i + 1 < len(merged):
                cand.append((len(merged[i + 1]), i + 1))
            cand.sort()
            for _, j in cand:
                if len(merged[j]) + len(p) + 1 < HARD_MAX:
                    if j < i:
                        merged[j] = merged[j] + "\n" + p
                    else:
                        merged[j] = p + "\n" + merged[j]
                    del merged[i]
                    changed = True
                    break
            if changed:
                break
    for i, p in enumerate(merged, 1):
        c = Chunk(**{**base.__dict__, "body": p, "part": i, "parts": len(merged)})
        chunks.append(c)


# --------------------------------------------------------------------------- 야구규칙

BR_FOOTER = re.compile(r"^\s*[․·]\s*\d+\s*[․·]\s*$")
BR_RUNHEAD = re.compile(r"^(?=.*\S\s{2,}\S)(?=.*(\d\.\d{2}|\d+(~\d+)?)).{0,40}$")
BR_HEAD = re.compile(r"^(\d)\.(\d{2}) (?! )(.*)$")
BR_GLOSS_ITEM = re.compile(r"^(\d{1,3})\.\s+([A-Z][A-Za-z0-9’'`\-\s\./]+?)\s*\(([^)]+)\)\s*$")


def clean_br_page(lines: list[str]) -> list[str]:
    out = [l for l in lines if not BR_FOOTER.match(l)]
    # 첫 두 줄 안에 러닝헤더(두 칸 공백 + 조항번호)가 있으면 제거
    for _ in range(2):
        for i, l in enumerate(out[:2]):
            if l.strip() and BR_RUNHEAD.match(l.strip()) and "  " in l.strip():
                del out[i]
                break
    return out


def parse_baseball_rules(pdf: Path) -> list[Chunk]:
    pages = page_lines(pdf)
    chunks: list[Chunk] = []
    src, sf = "BASEBALL_RULES", pdf.name

    # 1) 2026 변경 요약 (p7)
    # p7 표는 셀이 한 줄씩 떨어져 나오고 "구분" 셀이 두 줄에 걸치는 행이 있어 규칙적으로 못 읽는다.
    # 행 수가 2개뿐이라 2026판 기준으로 직접 적는다 — 판이 바뀌면 여기만 고칠 것.
    rows = [
        "- 야구도량형: 라인의 폭 3인치 → 라인의 폭 4인치(KBO)",
        "- 수비위치 5.02(C): (신설) → 수비 시프트 제한 강화",
    ]
    emit(chunks, Chunk(src, sf, "2026 변경 요약", "CHANGELOG-2026", "2026년 공식야구규칙 변경 요약사항",
                       "2026년 공식야구규칙에서 바뀐 내용 (변경 전 → 변경 후):\n" + "\n".join(rows), 7, 7, kind="CHANGELOG"))

    # 2) 본문 N.NN — p25 ~ 용어의 정의 직전
    body_pages = [(n, clean_br_page(l)) for n, l in pages if 25 <= n <= 220]
    gloss_start = next(n for n, ls in body_pages if any("<용어의 정의>" in x for x in ls))
    metric_start = next(n for n, ls in body_pages if any("<야구 도량형>" in x for x in ls))

    chapter = ""
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur and cur["lines"]:
            emit(chunks, Chunk(src, sf, cur["chapter"], cur["no"], cur["title"],
                               join_lines(cur["lines"]), cur["ps"], cur["pe"]))
        cur = None

    for n, ls in body_pages:
        if n >= gloss_start:
            break
        for l in ls:
            s = l.strip()
            if not s:
                continue
            m = BR_HEAD.match(s)
            if m:
                major, minor, rest = m.group(1), m.group(2), m.group(3).strip()
                no = f"{major}.{minor}"
                if minor == "00":
                    flush()
                    chapter = f"{no} {' '.join(rest.split())}"
                    continue
                flush()
                # 제목이 짧으면 제목, 길면(1.01처럼 본문이 바로 오면) 제목 없음 + 본문 시작
                if len(rest) <= 28 and not rest.endswith(("다.", "다")):
                    cur = {"chapter": chapter, "no": no, "title": rest, "lines": [], "ps": n, "pe": n}
                else:
                    cur = {"chapter": chapter, "no": no, "title": "", "lines": [rest], "ps": n, "pe": n}
                continue
            if cur is None:  # 장 제목 뒤 서두 등
                cur = {"chapter": chapter, "no": chapter.split()[0] if chapter else "0.00",
                       "title": "서두", "lines": [], "ps": n, "pe": n}
            cur["lines"].append(l)
            cur["pe"] = n
    flush()

    # 3) 용어의 정의
    gl_pages = [(n, ls) for n, ls in body_pages if gloss_start <= n < metric_start]
    item: dict | None = None

    def flush_g():
        nonlocal item
        if item and item["lines"]:
            emit(chunks, Chunk(src, sf, "용어의 정의", f"용어{item['no']}", item["title"],
                               join_lines(item["lines"]), item["ps"], item["pe"], kind="GLOSSARY"))
        item = None

    for n, ls in gl_pages:
        for l in ls:
            s = l.strip()
            if not s or "<용어의 정의>" in s or s.startswith("- Definitions of terms"):
                continue
            m = BR_GLOSS_ITEM.match(s)
            if m:
                flush_g()
                item = {"no": m.group(1), "title": f"{m.group(2).strip()} ({m.group(3).strip()})",
                        "lines": [], "ps": n, "pe": n}
                continue
            if item is None:
                continue
            item["lines"].append(l)
            item["pe"] = n
    flush_g()

    # 4) 야구 도량형 (표)
    mt = [(n, ls) for n, ls in body_pages if n >= metric_start and n <= 212]
    lines = [l for _, ls in mt for l in ls if l.strip() and "<야구 도량형>" not in l]
    text = " / ".join(re.sub(r"\s+", " ", l.strip()) for l in lines)
    emit(chunks, Chunk(src, sf, "부록", "야구도량형", "야구 도량형 환산표", text,
                       mt[0][0], mt[-1][0], kind="TABLE"))
    return chunks


# --------------------------------------------------------------------------- 리그규정

LR_FOOTER = re.compile(r"^\s*\d{1,3}\s*$")
LR_CHAPTER = re.compile(r"^제(\d+)장\s+(?!제\d+조)(.+)$")
LR_ARTICLE = re.compile(r"^제(\d+)조(?:의\s?(\d+))?\s+(\S.*)$")
LR_APPENDICES = [
    "고척스카이돔 그라운드룰",
    "2026년 구단별 경기관리인",
    "자동 투구 판정 시스템(ABS) 규정",
    "경기의 스피드업",
    "KBO 피치클락 규정",
    "기록 이의신청 심의 제도",
    "경기 중 선수단 행동 관련 지침",
    "벌칙내규",
    "KBO 표창규정",
]
LR_NUM_ITEM = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")

# 표가 있는 조 — PDF 병합셀이라 셀 순서만으로는 행을 복원할 수 없어 2026판 기준으로 직접 적는다 (p24 대조, 2026-09-23).
# 표 아래의 주석(*) 문장과 개정 이력은 원문에서 그대로 이어붙인다. 판이 바뀌면 여기만 고칠 것.
LR_TABLE_OVERRIDE = {
    "제21조": (
        "경기개시 시간 (토요일 / 일요일·공휴일 / 평일):\n"
        "- 3월·4월·5월: 토요일 17:00 / 일요일·공휴일 14:00 / 평일 18:30\n"
        "- 6월: 토요일 17:00 / 일요일·공휴일 17:00 / 평일 18:30\n"
        "- 7월·8월: 토요일 18:00 / 일요일·공휴일 18:00 / 평일 18:30\n"
        "- 9월 1일~14일: 토요일 17:00 / 일요일·공휴일 17:00 / 평일 18:30\n"
        "- 9월 15일~30일·10월: 토요일 17:00 / 일요일·공휴일 14:00 / 평일 18:30"
    ),
}


def clean_lr_page(lines: list[str]) -> list[str]:
    out = [l for l in lines if l.strip()]
    if out and LR_FOOTER.match(out[0]):
        out = out[1:]
    return out


def parse_league_regulations(pdf: Path) -> list[Chunk]:
    pages = page_lines(pdf)
    chunks: list[Chunk] = []
    src, sf = "LEAGUE_REGULATIONS", pdf.name
    body = [(n, clean_lr_page(l)) for n, l in pages if 11 <= n <= 102]

    # 부록 시작 페이지 찾기: 페이지 첫 줄이 부록 제목으로 시작
    app_start: dict[int, str] = {}
    for n, ls in body:
        if ls and n >= 65:
            first = ls[0].strip()
            for t in LR_APPENDICES:
                if first.startswith(t) and n not in app_start:
                    app_start[n] = t
    first_app = min(app_start)

    # ---- 본문 (전문 + 제N장/제N조)
    chapter, cur = "", None

    def flush():
        nonlocal cur
        if cur and cur["lines"]:
            text = join_lines(cur["lines"])
            if cur["no"] in LR_TABLE_OVERRIDE:
                # 표 셀 부분(첫 "*" 주석 앞까지)을 구조화 문장으로 바꾸고 주석·개정이력은 원문 유지
                star = text.find("*")
                text = LR_TABLE_OVERRIDE[cur["no"]] + ("\n" + text[star:] if star >= 0 else "")
            emit(chunks, Chunk(src, sf, cur["chapter"], cur["no"], cur["title"],
                               text, cur["ps"], cur["pe"], kind=cur.get("kind", "ARTICLE")))
        cur = None

    for n, ls in body:
        if n >= first_app:
            break
        for l in ls:
            s = l.strip()
            mc = LR_CHAPTER.match(s)
            if mc:
                flush(); chapter = f"제{mc.group(1)}장 {mc.group(2).strip()}"; continue
            ma = LR_ARTICLE.match(s)
            if ma and len(ma.group(3)) <= 40:
                flush()
                no = f"제{ma.group(1)}조" + (f"의{ma.group(2)}" if ma.group(2) else "")
                cur = {"chapter": chapter, "no": no, "title": ma.group(3).strip(), "lines": [], "ps": n, "pe": n}
                continue
            if cur is None:
                cur = {"chapter": "전문", "no": "전문", "title": "목적", "lines": [], "ps": n, "pe": n, "kind": "PREAMBLE"}
            cur["lines"].append(l); cur["pe"] = n
    flush()

    # ---- 부록: 섹션별로 모은 뒤 (표창규정) 제N조 / (그 외) 1. 항목 단위
    starts = sorted(app_start)
    for i, sp in enumerate(starts):
        title = app_start[sp]
        ep = starts[i + 1] - 1 if i + 1 < len(starts) else 102
        sec_lines: list[tuple[int, str]] = []
        for n, ls in body:
            if sp <= n <= ep:
                for l in ls:
                    sec_lines.append((n, l))
        # 제목 줄 제거 (첫 페이지 첫 줄, 두 줄에 걸친 제목은 TOC 기준으로 잘라냄)
        sec_lines = sec_lines[1:]
        if title.startswith("2026년 구단별"):
            sec_lines = [x for x in sec_lines if x[1].strip() not in ("경기관리 대리인, 스피드업 및", "도핑·경기사용구 담당자")]
        short = {"고척스카이돔 그라운드룰": "고척그라운드룰", "2026년 구단별 경기관리인": "경기관리인명단",
                 "자동 투구 판정 시스템(ABS) 규정": "ABS", "경기의 스피드업": "스피드업",
                 "KBO 피치클락 규정": "피치클락", "기록 이의신청 심의 제도": "기록이의신청",
                 "경기 중 선수단 행동 관련 지침": "선수단행동지침", "벌칙내규": "벌칙내규",
                 "KBO 표창규정": "표창규정"}[title]
        section = f"부록 {title}"

        if title in ("고척스카이돔 그라운드룰", "2026년 구단별 경기관리인"):
            text = " / ".join(re.sub(r"\s+", " ", l.strip()) for _, l in sec_lines if l.strip())
            emit(chunks, Chunk(src, sf, section, f"APP-{short}", title, text,
                               sec_lines[0][0], sec_lines[-1][0], kind="TABLE"))
            continue

        head_re = LR_ARTICLE if title == "KBO 표창규정" else LR_NUM_ITEM
        items: list[dict] = []
        cur = None
        sub = ""      # 벌칙내규의 "감독, 코치, 선수" / "심판위원" / "기타" 소제목
        last_no = 0   # 번호 항목은 직전 번호 +1 일 때만 제목으로 인정 (표 안의 "제10조 …" 오탐 방지)
        for n, l in sec_lines:
            s_ = l.strip()
            m = head_re.match(s_)
            num = int(m.group(1)) if m else None
            if m and num == last_no + 1:
                if cur: items.append(cur)
                last_no = num
                if title == "KBO 표창규정":
                    no, ttl, first = f"표창규정-제{num}조", m.group(3).strip(), []
                else:
                    # "1. 감독, 코치 또는 선수가 …" 처럼 번호 뒤가 제목이 아니라 문장이므로 본문에 그대로 둔다
                    no, ttl, first = f"{short}-{sub + '-' if sub else ''}{num}", "", [l]
                cur = {"chapter": section + (f" > {sub}" if sub else ""), "no": no, "title": ttl,
                       "lines": first, "ps": n, "pe": n, "kind": "APPENDIX"}
                continue
            if title == "벌칙내규" and s_ in ("감독, 코치, 선수", "심판위원", "기타", "구단"):
                if cur: items.append(cur)
                cur, sub, last_no = None, s_, 0
                continue
            if cur is None:
                cur = {"chapter": section, "no": f"{short}-서두", "title": title, "lines": [], "ps": n, "pe": n, "kind": "APPENDIX"}
            cur["lines"].append(l); cur["pe"] = n
        if cur: items.append(cur)

        # 짧은 항목은 같은 소절 안에서 이웃과 합침 (선수단 행동지침처럼 한 줄짜리 항목 대비)
        merged: list[dict] = []
        for it in items:
            it["text"] = join_lines(it["lines"])
            prev = merged[-1] if merged else None
            if (prev and prev["chapter"] == it["chapter"]
                    and title != "KBO 표창규정"          # 제N조는 조 단위 유지 (홀드규정·신인상이 다른 조에 묻히지 않게)
                    and (len(prev["text"]) < MIN_MERGE or len(it["text"]) < MIN_MERGE)
                    and len(prev["text"]) + len(it["text"]) <= MAX_CHARS
                    and not prev["no"].endswith("서두")):
                prev["text"] += "\n" + it["text"]
                prev["pe"] = it["pe"]
                a = prev["no"].rsplit("-", 1); b = it["no"].rsplit("-", 1)[-1]
                prev["no"] = f"{a[0]}-{a[1].split('~')[0]}~{b}"
                if prev["title"] and not prev["title"].endswith(" 외"):
                    prev["title"] += " 외"
                continue
            merged.append(it)
        for it in merged:
            emit(chunks, Chunk(src, sf, it["chapter"], it["no"], it["title"], it["text"],
                               it["ps"], it["pe"], kind="APPENDIX"))
    return chunks


# --------------------------------------------------------------------------- 출력

DOC_LABEL = {"BASEBALL_RULES": "공식야구규칙", "LEAGUE_REGULATIONS": "KBO 리그규정"}
SRC_KEY = {"BASEBALL_RULES": "BR", "LEAGUE_REGULATIONS": "LR"}


def content_of(c: Chunk) -> str:
    head = f"[{DOC_LABEL[c.source]} 2026]"
    if c.section:
        head += f" {c.section} >"
    head += f" {c.article_no}" if not c.article_no.startswith(("APP-", "용어", "CHANGELOG")) else ""
    head += f" {c.article_title}" if c.article_title else ""
    if c.parts > 1:
        head += f" ({c.part}/{c.parts})"
    return head.strip() + "\n" + c.body


def doc_id_of(c: Chunk) -> str:
    key = re.sub(r"[^0-9A-Za-z가-힣\.]+", "_", c.article_no).strip("_")
    did = f"RULE_COMMON_{SRC_KEY[c.source]}_{key}"
    if c.parts > 1:
        did += f"_p{c.part}"
    return did


def write_csv(chunks: list[Chunk], out: Path) -> None:
    cols = ["doc_id", "category", "source_doc", "source_file", "edition", "kind", "section",
            "article_no", "article_title", "part", "parts", "page_start", "page_end",
            "evidence_type", "status", "content_hash", "content"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in chunks:
            content = content_of(c)
            w.writerow({
                "doc_id": doc_id_of(c), "category": "RULE", "source_doc": c.source,
                "source_file": c.source_file, "edition": "2026", "kind": c.kind, "section": c.section,
                "article_no": c.article_no, "article_title": c.article_title,
                "part": c.part, "parts": c.parts, "page_start": c.page_start, "page_end": c.page_end,
                "evidence_type": "OFFICIAL", "status": "CONFIRMED",
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                "content": content,
            })


# (파일, 파서) — 분할 상수는 위 모듈 기본값을 두 문서에 똑같이 쓴다.
SOURCES = [
    ("2026_KBO야구규칙.pdf", parse_baseball_rules),      # 조가 길고 ⒜⑴ 계층 깊음
    ("2026_KBO리그규정.pdf", parse_league_regulations),  # 조 짧고 표·항목 많음
    # 야구규약은 제외 (2026-09-23) — 넣으려면 ("2026_KBO야구규약.pdf", parse_kbo_agreement) 형태로 파서 추가
]


def apply_cfg(cfg: dict | None) -> None:
    """분할 상수를 바꾼다 (None 이면 그대로). rag_test/exp_chunking.py 가 변형을 만들 때만 쓴다."""
    global MAX_CHARS, HARD_MAX, MIN_MERGE
    if cfg:
        MAX_CHARS, HARD_MAX, MIN_MERGE = cfg["MAX_CHARS"], cfg["HARD_MAX"], cfg["MIN_MERGE"]


def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--raw", default=str(root / "data" / "raw" / "rules"))
    ap.add_argument("--out", default=str(root / "data" / "preprocessed" / "kbo_rulebook_chunks.csv"))
    a = ap.parse_args()
    chunks: list[Chunk] = []
    for fname, fn in SOURCES:
        got = fn(Path(a.raw) / fname)
        print(f"{fname}: {len(got)} chunks")
        chunks.extend(got)
    ids = [doc_id_of(c) for c in chunks]
    dup = len(ids) - len(set(ids))
    write_csv(chunks, Path(a.out))
    lens = sorted(len(content_of(c)) for c in chunks)
    print(f"total={len(chunks)} dup_doc_id={dup} len min/med/p90/max = "
          f"{lens[0]}/{lens[len(lens)//2]}/{lens[int(len(lens)*.9)]}/{lens[-1]} -> {a.out}")


if __name__ == "__main__":
    main()
