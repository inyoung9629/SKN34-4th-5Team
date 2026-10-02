"""청킹 변형 비교 실험 — 규정집 (4차 #14 QA, 2026-09-23).

chunk_rulebooks.py 의 분할 상수(MAX_CHARS / HARD_MAX / MIN_MERGE)와 헤더 유무만 바꿔 변형을 만들고,
golden_rule.jsonl 로 "정답 조항이 상위 5개 안에 오는가"를 잰다. DB 없이 인메모리(numpy)로 돌린다.

- 매칭은 **조 단위**: doc_id 끝의 _pN 을 떼고 비교 (변형마다 파트 번호가 달라지므로)
- R0 = 벡터만 / R3 = 벡터 상위 --cand → keyword_rerank(alpha 0.3) 상위 5  (STEP4 의 R0·R3 과 같은 구성)
- 청크 임베딩은 results/chunk_cache.json 에 내용 해시로 캐시 → 변형 간 중복 청크는 다시 안 부름

실행: python rag_test/exp_chunking.py [--variants A,C,F] [--k 5]
결과: results/rule/chunking_<시각>.csv (변형×질문 순위) + 콘솔 요약표
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from common import EMBED_MODEL, RESULTS, embed, keyword_rerank, load_golden, openai_client, results_dir  # noqa: E402
from scoring import hit_at, mrr, rank_of  # noqa: E402

# chunk_rulebooks 를 파일로 직접 로드 (backend 패키지 import 없이)
_spec = importlib.util.spec_from_file_location("chunk_rulebooks", ROOT / "backend" / "preprocessing" / "chunk_rulebooks.py")
CR = importlib.util.module_from_spec(_spec)
sys.modules["chunk_rulebooks"] = CR   # dataclass 가 모듈을 sys.modules 에서 찾음 (py3.10)
_spec.loader.exec_module(CR)
RAW = ROOT / "data" / "raw" / "rules"

# ── 변형 정의 ────────────────────────────────────────────────────────────────
# header=False 면 "[공식야구규칙 2026] 5.00 … > 5.06 주루 (2/9)" 줄 없이 본문만 임베딩
VARIANTS = {
    "A_article_only":   dict(MAX_CHARS=10**9, HARD_MAX=10**9, MIN_MERGE=0,   header=True),   # 조 단위, 분할 없음
    "B_800":            dict(MAX_CHARS=800,   HARD_MAX=1200,  MIN_MERGE=200, header=True),
    "C_1200":           dict(MAX_CHARS=1200,  HARD_MAX=1800,  MIN_MERGE=250, header=True),   # 1차 실험 당시 기본값
    "D_1600":           dict(MAX_CHARS=1600,  HARD_MAX=2400,  MIN_MERGE=300, header=True),
    "E_clause_all":     dict(MAX_CHARS=400,   HARD_MAX=600,   MIN_MERGE=0,   header=True),   # 사실상 항 단위 전부
    "F_1200_noheader":  dict(MAX_CHARS=1200,  HARD_MAX=1800,  MIN_MERGE=250, header=False),  # C 와 같되 헤더 없음
    "H_1400":           dict(MAX_CHARS=1400,  HARD_MAX=2100,  MIN_MERGE=250, header=True),   # 현재 채택값
}
# 걷어낸 변형 (2026-09-23 3차):
#   G_1600_fixed  — D_1600 과 설정이 같아 결과가 완전히 동일했다. 청커 수정 전/후 비교는 1508 과 1519 결과 파일을 맞대면 된다.
#   I_doctype     — 문서별 차등(야구규칙 1600 / 리그규정 1200). H_1400 과 31문항 순위가 하나도 다르지 않아 per_source 기능째로 제거.


def build_variant(cfg):
    """chunk_rulebooks 모듈 상수를 바꿔 청크를 만든다. 반환: [(doc_id, article_id, text)]"""
    out = []
    CR.apply_cfg(cfg)
    for fname, fn in CR.SOURCES:
        for c in fn(RAW / fname):
            doc_id = CR.doc_id_of(c)
            text = CR.content_of(c) if cfg["header"] else c.body
            out.append((doc_id, re.sub(r"_p\d+$", "", doc_id), text))
    return out


# ── 청크 임베딩 (내용 해시 캐시) ─────────────────────────────────────────────
def embed_chunks(texts, batch=100):
    LIMIT = 7000   # text-embedding-3-small 8,191토큰 한도. 넘는 청크는 앞부분만 (A 변형에서만 발생)
    truncated = sum(len(t) > LIMIT for t in texts)
    texts = [t[:LIMIT] for t in texts]
    cache_path = RESULTS / "chunk_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    key = lambda t: hashlib.sha1(f"{EMBED_MODEL}|{t}".encode()).hexdigest()
    todo = [t for t in dict.fromkeys(texts) if key(t) not in cache]
    called = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        resp = openai_client().embeddings.create(model=EMBED_MODEL, input=part)
        for t, d in zip(part, resp.data):
            cache[key(t)] = d.embedding
        called += len(part)
    if called:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    M = np.array([cache[key(t)] for t in texts], dtype=np.float32)
    M /= np.linalg.norm(M, axis=1, keepdims=True)
    return M, called, truncated


def article_patterns(gold):
    """골든의 gold 정규식에서 _p\\d+ / _p2 꼬리를 떼서 조 단위 패턴으로"""
    return [re.sub(r"_p(\\d\+|\d+)$", "", p) for p in gold]


# ── 실행 ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="golden_rule.jsonl")
    ap.add_argument("--variants", default=",".join(VARIANTS), help="쉼표 구분, 접두 글자만 써도 됨 (A,C,F)")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--cand", type=int, default=50, help="하이브리드 rerank 후보 수 (운영값 50 — agent.py search(k=))")
    args = ap.parse_args()

    golden = [q for q in load_golden(args.golden) if q["expect"] == "answer"]
    qvecs = np.array(embed([q["question"] for q in golden]), dtype=np.float32)
    qvecs /= np.linalg.norm(qvecs, axis=1, keepdims=True)

    sel = set(args.variants.split(","))
    picked = [v for v in VARIANTS if v in sel or v.split("_")[0] in sel]
    rows_out, summary = [], []
    for name in picked:
        cfg = VARIANTS[name]
        t0 = time.time()
        chunks = build_variant(cfg)
        texts = [t for _, _, t in chunks]
        M, called, truncated = embed_chunks(texts)
        sims = qvecs @ M.T                                    # (질문, 청크)
        ranks0, ranks3, hits1 = [], [], 0
        for qi, q in enumerate(golden):
            pats = article_patterns(q["gold"])
            order = np.argsort(-sims[qi])[:args.cand]
            cand = [{"doc_id": chunks[j][0], "article": chunks[j][1], "content": chunks[j][2], "dist": float(1 - sims[qi][j])} for j in order]
            # 같은 조가 여러 파트로 잡히면 첫 파트만 순위에 세움 (조 단위 Hit@k)
            def dedupe(rs):
                seen, out = set(), []
                for r in rs:
                    if r["article"] not in seen:
                        seen.add(r["article"]); out.append(r["article"])
                return out
            r0 = rank_of(dedupe(cand)[:args.k], pats)
            r3 = rank_of(dedupe(keyword_rerank(q["question"], cand, k=args.cand)) [:args.k], pats)
            ranks0.append(r0); ranks3.append(r3)
            rows_out.append({"variant": name, "id": q["id"], "question": q["question"],
                             "rank_vector": r0 or "", "rank_hybrid": r3 or "",
                             "top1_vector": dedupe(cand)[0], "top1_hybrid": dedupe(keyword_rerank(q["question"], cand, k=args.cand))[0]})
        lens = [len(t) for t in texts]
        summary.append({"variant": name, "chunks": len(chunks), "avg_len": int(np.mean(lens)), "p90_len": int(np.percentile(lens, 90)),
                        "R0_hit1": hit_at(ranks0, 1), "R0_hit5": hit_at(ranks0, args.k), "R0_mrr": mrr(ranks0),
                        "R3_hit1": hit_at(ranks3, 1), "R3_hit5": hit_at(ranks3, args.k), "R3_mrr": mrr(ranks3),
                        "embed_calls": called, "truncated": truncated, "sec": round(time.time() - t0, 1)})
        s = summary[-1]
        print(f"{name:18s} chunks={s['chunks']:4d} avg={s['avg_len']:4d} p90={s['p90_len']:4d} | "
              f"R0 hit@1={s['R0_hit1']:.3f} hit@5={s['R0_hit5']:.3f} mrr={s['R0_mrr']:.3f} | "
              f"R3 hit@1={s['R3_hit1']:.3f} hit@5={s['R3_hit5']:.3f} mrr={s['R3_mrr']:.3f} | embed={called} trunc={truncated} {s['sec']}s")

    out = results_dir(args.golden) / f"chunking_{time.strftime('%m%d_%H%M')}.csv"
    import csv
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0])); w.writeheader(); w.writerows(rows_out)
    with out.with_suffix(".summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("→", out)

    # 변형별로 놓친 문항
    for name in picked:
        miss = [r["id"] for r in rows_out if r["variant"] == name and not r["rank_hybrid"]]
        if miss:
            print(f"  {name} R3 miss: {miss}")


if __name__ == "__main__":
    main()
