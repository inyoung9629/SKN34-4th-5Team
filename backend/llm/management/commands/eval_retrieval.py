r"""문서 검색 성능을 골든셋으로 잰다: 검색 방식(RAG_SEARCH_MODE)별 Hit@1·Hit@5·MRR (LLM 호출 0회, 질문 임베딩만).

  # 골든셋은 레포의 rag_test/golden 에 있다(컨테이너 밖) → 표준 입력으로 넘긴다
  docker compose exec -T backend python manage.py eval_retrieval --label rule  < rag_test\golden\golden_rule.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label venue < rag_test\golden\golden_venue.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label club  < rag_test\golden\golden_club.jsonl

  # 구장 안팎 장소 검색 경로(search_documents_tool)로 재기, RRF 상수 비교
  docker compose exec -T backend python manage.py eval_retrieval --label venue --path venue < rag_test\golden\golden_venue.jsonl
  docker compose exec -T backend python manage.py eval_retrieval --label rule --rrf-k 20,60,100 < rag_test\golden\golden_rule.jsonl

--path kbo  : search_kbo_documents 경로(규정·구장 문서). 구장·분류는 v1 router 규칙(rag_test STEP4 R3 와 같은 조건)
--path venue: search_documents_tool 경로(구장 안팎 장소). 구장·분류는 infer_slots, 검색어 변환(LLM)은 건너뛴다
문항별 결과 CSV: artifacts/rag_eval/
"""
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.tools import knowledge


def rank_of(doc_ids, patterns):
    for i, doc_id in enumerate(doc_ids, 1):
        if any(re.fullmatch(p, doc_id or "") for p in patterns):
            return i
    return None


def summary(ranks, times):
    n = len(ranks) or 1
    return {"Hit@1": sum(1 for r in ranks if r == 1) / n, "Hit@5": sum(1 for r in ranks if r and r <= 5) / n,
            "MRR": sum(1 / r for r in ranks if r) / n, "P50ms": statistics.median(times) if times else 0}


class Command(BaseCommand):
    help = "골든셋으로 문서 검색 방식(vector·hybrid·rerank 등)을 비교한다."

    def add_arguments(self, parser):
        parser.add_argument("--golden", default="-", help="골든셋 jsonl 경로, '-' 면 표준 입력")
        parser.add_argument("--label", default="golden")
        parser.add_argument("--modes", default="vector,hybrid,hybrid_rerank,hybrid_score")
        parser.add_argument("-k", type=int, default=5)
        parser.add_argument("--path", choices=("kbo", "venue"), default="kbo")
        parser.add_argument("--rrf-k", default="", help="RRF 상수들 (예: 20,60,100)")
        parser.add_argument("--rrf-sparse-weight", default="", help="가중 RRF 의 키워드 목록 가중치들 (예: 0.3,0.5,1)")
        parser.add_argument("--sparse-weight", default="", help="hybrid_score 의 키워드 가중치들 (예: 0,0.1,0.3)")
        parser.add_argument("--rerank-model", default="", help="rerank·vector_rerank 의 리랭커 모델들 (쉼표)")
        parser.add_argument("--detail", action="store_true", help="이전 방식보다 나빠진 문항의 순위와 1위 문서를 보여 준다")

    def runs(self, o, modes):
        from llm import reranker, sparse_index
        grid = {
            "rrf_k": [int(v) for v in o["rrf_k"].split(",") if v.strip()] or [sparse_index.RRF_K],
            "rrf_w": [float(v) for v in o["rrf_sparse_weight"].split(",") if v.strip()] or [sparse_index.SPARSE_RRF_WEIGHT],
            "w": [float(v) for v in o["sparse_weight"].split(",") if v.strip()] or [sparse_index.SPARSE_WEIGHT],
            "model": [v.strip() for v in o["rerank_model"].split(",") if v.strip()] or [reranker.model_name()],
        }
        uses = {"hybrid": ("rrf_k", "rrf_w"), "hybrid_rerank": ("rrf_k", "rrf_w"), "hybrid_score": ("w",),
                "rerank": ("rrf_k", "rrf_w", "model"), "vector_rerank": ("model",), "vector": ()}
        out = []
        for mode in modes:
            combos = [{}]
            for param in uses[mode]:
                combos = [{**c, param: v} for c in combos for v in grid[param]]
            for combo in combos:
                label = "@".join([mode] + [f"{p}={v}" for p, v in combo.items() if len(grid[p]) > 1])
                out.append((label, mode, combo))
        return out

    def handle(self, *args, **o):
        from llm import reranker, sparse_index
        from llm.v1.rag.club.retrieval import embed_many
        from llm.v1.rag.club.router import detect_categories, detect_stadium
        text = sys.stdin.read() if o["golden"] == "-" else Path(o["golden"]).read_text(encoding="utf-8")
        golden = [q for q in (json.loads(line) for line in text.splitlines() if line.strip()) if q.get("gold")]
        if not golden:
            raise CommandError("gold 가 있는 문항이 없습니다 (jsonl 을 표준 입력이나 --golden 으로 넘기세요)")
        modes = [m for m in o["modes"].split(",") if m in knowledge.SEARCH_MODES]
        vectors = embed_many([q["question"] for q in golden])
        rows, results, tops = [], {}, {}
        for name, mode, combo in self.runs(o, modes):
            sparse_index.RRF_K = combo.get("rrf_k", sparse_index.RRF_K)
            sparse_index.SPARSE_RRF_WEIGHT = combo.get("rrf_w", sparse_index.SPARSE_RRF_WEIGHT)
            sparse_index.SPARSE_WEIGHT = combo.get("w", sparse_index.SPARSE_WEIGHT)
            if "model" in combo:
                os.environ["RAG_RERANK_MODEL"] = combo["model"]
            reranker.COSTS.clear()
            ranks, times, firsts = [], [], []
            for q, vec in zip(golden, vectors):
                started = time.perf_counter()
                if o["path"] == "venue":
                    docs, _ = knowledge.retrieve_venue(q["question"], knowledge.infer_slots(q["question"]), mode=mode, qvec=vec)
                    ids = [(d.get("metadata") or {}).get("doc_id") for d in docs[:o["k"]]]
                else:
                    hits = knowledge.search_kbo_rows(q["question"], detect_stadium(q["question"]),
                                                     detect_categories(q["question"]) or None, o["k"],
                                                     _embed=lambda _text, vec=vec: vec, mode=mode)
                    ids = [h.get("doc_id") for h in hits]
                times.append((time.perf_counter() - started) * 1000)
                rank = rank_of(ids, q["gold"])
                ranks.append(rank)
                firsts.append(ids[0] if ids else "-")
                rows.append({"mode": name, "id": q["id"], "group": q.get("group", ""), "question": q["question"],
                             "rank": rank or "miss", "top": " | ".join(map(str, ids))})
            results[name] = (summary(ranks, times), ranks, sum(c for _, c in reranker.COSTS))
            tops[name] = firsts
        self.stdout.write(f"[{o['label']}] {len(golden)}문항 · 경로={o['path']} · k={o['k']}")
        self.stdout.write("| 방식 | Hit@1 | Hit@5 | MRR | P50ms | 리랭커 비용 |\n|---|---|---|---|---|---|")
        for name, (s, _, cost) in results.items():
            self.stdout.write(f"| {name} | {s['Hit@1']:.3f} | {s['Hit@5']:.3f} | {s['MRR']:.3f} | {s['P50ms']:.0f} | "
                              f"{'$%.4f' % cost if cost else '-'} |")
        if "vector" in results:
            base = results["vector"][1]
            for name, (_, ranks, _) in results.items():
                if name == "vector":
                    continue
                better = [q["id"] for q, a, b in zip(golden, base, ranks) if (b or 99) < (a or 99)]
                worse = [q["id"] for q, a, b in zip(golden, base, ranks) if (b or 99) > (a or 99)]
                self.stdout.write(f"{name} vs vector: 좋아짐 {better or '-'} / 나빠짐 {worse or '-'}")
                if o["detail"]:
                    for q, a, b, first in zip(golden, base, ranks, tops[name]):
                        if (b or 99) > (a or 99):
                            self.stdout.write(f"    {q['id']} {q['question'][:30]} | 순위 {a or '밖'} → {b or '밖'} | 1위 {first}")
        out = Path(settings.BASE_DIR) / "artifacts" / "rag_eval"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{o['label']}_{datetime.now():%m%d_%H%M}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(f"문항별 결과: {path}")
