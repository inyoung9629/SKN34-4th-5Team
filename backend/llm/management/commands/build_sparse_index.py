"""하이브리드 검색용 키워드(BM25) 색인을 문서 컬렉션에서 다시 만든다 (llm.sparse_index, 임베딩·LLM 비용 없음).

  docker compose exec backend python manage.py build_sparse_index --dry-run   # 문서 종류별 건수·토큰 예시만
  docker compose exec backend python manage.py build_sparse_index

문서 컬렉션(kbo_documents_openai)은 읽기만 한다. 이후 build_index·크롤러가 넣는 문서는 sparse 색인에도 자동으로 들어간다.
문서 종류별 건수로 #5 완료 기준 3(구장·좌석·규정·먹거리·응원·야구 규칙 문서가 모두 색인에 있는지)을 확인한다.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from llm import sparse_index

GROUPS = {  # 이슈 #5 의 문서 분류 → 색인 category
    "구장": ("STADIUM", "OPERATION", "FACILITY", "TRANSPORT", "CONTENT"),
    "좌석·티켓": ("SEAT", "PRICE", "TICKET_POLICY"),
    "규정·야구 규칙": ("RULE", "CARRY_IN", "REENTRY"),
    "먹거리": ("FOOD_IN", "FOOD_OUT", "CAFE"),
    "주변": ("SPOT",),
    "일정·순위": ("SCHEDULE", "STANDING"),
}


class Command(BaseCommand):
    help = "Qdrant 문서 컬렉션에서 한국어 키워드(BM25) sparse 색인을 다시 만든다."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="색인을 만들지 않고 문서 종류별 건수·토큰 예시만 본다")

    def handle(self, *args, **o):
        from llm.vector_store import iter_documents
        docs = list(iter_documents())
        categories = Counter((d.get("metadata") or {}).get("category") or "(없음)" for d in docs)
        known = {c for cats in GROUPS.values() for c in cats}
        for group, cats in GROUPS.items():
            self.stdout.write(f"{group:<10} {sum(categories[c] for c in cats):>5}건  " + ", ".join(f"{c} {categories[c]}" for c in cats if categories[c]))
        others = {c: n for c, n in categories.items() if c not in known}
        if others:
            self.stdout.write(f"{'기타':<10} {sum(others.values()):>5}건  " + ", ".join(f"{c} {n}" for c, n in others.items()))
        cheer = sum("응원" in (d.get("content") or "") for d in docs)
        self.stdout.write(f"응원 언급 문서 {cheer}건 (응원 전용 문서 분류는 아직 없음)")
        if o["dry_run"]:
            lengths = [len(sparse_index.tokens(d.get("content") or "")) for d in docs]
            for doc in docs[:3]:
                self.stdout.write(f"{doc.get('external_id')}: {sparse_index.tokens(doc.get('content') or '')[:20]}")
            self.stdout.write(f"[미리보기] 문서 {len(docs)}개 | 평균 토큰 {sum(lengths) / max(len(lengths), 1):.1f}개 → {sparse_index.collection_name()}")
            return
        done, average = sparse_index.build_from_documents(report=lambda line: None)
        self.stdout.write(self.style.SUCCESS(f"sparse 색인 완료: 문서 {done}개 | 평균 토큰 {average:.1f}개 → {sparse_index.collection_name()}"))
