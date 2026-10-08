"""RAG 가 제대로 붙었는지 한 번에 확인 — shell 에 붙여넣기 없이 한 줄로.

    docker compose exec backend python manage.py rag_check
    docker compose exec backend python manage.py rag_check --invoke -q "잠실 코스 짜줘"
    docker compose exec backend python manage.py rag_check --invoke --course    # 코스 추천까지 확인

확인 항목
    1) 환경변수   CHAT_USE_RAG · LLM_MODEL · EMBEDDING_MODEL · LANGSMITH_*
    2) 스위치     use_rag() / chat_chain() — 지금 챗봇이 RAG 로 도는지
    3) 인덱스     Qdrant 청크 건수 (0 이면 build_index 필요)
    4) 실제 호출 (--invoke 명시 시만) 질문 하나를 끝까지 태워 본다 (reasoning_effort 등 모델 파라미터 검증 포함)
"""
import os
import time

from django.core.management.base import BaseCommand, CommandError
from llm.vector_store import ensure_collection, iter_documents

MASK = ("API_KEY", "PASSWORD", "SECRET", "SIGNING")


def show(k):
    v = os.getenv(k)
    if v is None:
        return "(없음)"
    if any(m in k for m in MASK):
        return f"설정됨 ({len(v)}자)" if v else "(빈 값)"
    return v or "(빈 값)"


class Command(BaseCommand):
    help = "RAG 연결 상태를 점검한다 (환경변수 · 스위치 · 인덱스 · 실제 호출)"

    def add_arguments(self, parser):
        parser.add_argument("--invoke", action="store_true", help="유료 embedding/LLM API로 실제 질문을 실행한다")
        parser.add_argument("-q", "--question", default="LG 지금 몇 위야?")
        parser.add_argument("--course", action="store_true", help="코스 추천 질문으로 확인")
        parser.add_argument("--stadium", default=None, help='예: "잠실야구장"')
        parser.add_argument("--twice", action="store_true",
                            help="같은 프로세스에서 두 번 호출해 콜드/웜 차이를 본다 (실서버는 웜 상태로 돈다)")

    def handle(self, *a, **o):
        ok, ng = self.style.SUCCESS, self.style.ERROR
        p = self.stdout.write

        p("\n[1] 환경변수")
        for k in ("CHAT_USE_RAG", "LLM_MODEL", "EMBEDDING_MODEL",
                  "LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
            p(f"    {k:<20} {show(k)}")

        p("\n[2] 스위치")
        try:
            from llm.v1.rag.pipeline import chat_chain, use_rag
        except Exception as e:
            raise CommandError(f"pipeline import 실패: {e!r}") from e
        on = use_rag()
        p(f"    use_rag()            {ok('True  → RAG 로 답한다') if on else ng('False → 예전 helpful-assistant 체인')}")
        p(f"    chat_chain()         {type(chat_chain()).__name__ if chat_chain() else 'None'}")
        if not on:
            p("    (CHAT_USE_RAG=1 로 두고 backend 를 restart 하세요)")

        p("\n[3] Qdrant 인덱스")
        try:
            from collections import Counter
            name = ensure_collection()
            categories = Counter()
            n = 0
            food = stadium = None
            for doc in iter_documents():
                m = doc["metadata"]
                n += 1
                categories[m.get("category")] += 1
                if food is None and m.get("category") == "FOOD_OUT":
                    food = m
                if stadium is None and m.get("category") == "STADIUM" and m.get("stadium_code") == "JAMSIL":
                    stadium = m
            p(f"    {name}: 청크 {n} 건")
            for category, count in categories.most_common(8):
                p(f"      {category or '(없음)'}: {count}")
            if not n:
                raise CommandError("Qdrant document collection is empty; migrate/build_index first")
            p("\n[3-1] 코스용 메타데이터")
            if food:
                need = ("name", "lat_y", "lng_x", "distance_m", "category_detail", "kakao_place_id", "address", "stadium_code", "doc_id")
                missing = [key for key in need if not food.get(key)]
                p(f"    FOOD_OUT 필요 키: {missing or '전부 있음'}")
            else:
                p(ng("    FOOD_OUT 청크 없음"))
            p(f"    JAMSIL STADIUM: {stadium.get('stadium_name_ko')}" if stadium else ng("    JAMSIL STADIUM 청크 없음"))
        except Exception as exc:
            raise CommandError(f"Qdrant diagnostic failed: {exc}") from exc

        if not o["invoke"]:
            p(ok("\n읽기 전용 점검 완료 — 실제 유료 호출은 --invoke로 명시하세요"))
            return

        p("\n[4] 실제 호출")
        q = "잠실에서 친구들이랑 첫 직관인데 경기 전후 코스 짜줘. 치킨 좋아해" if o["course"] else o["question"]
        p(f"    질문: {q}")
        try:
            from llm.v1.rag.pipeline import answer
            t0 = time.perf_counter()
            r = answer(q, stadium_name=o["stadium"])
            ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            raise CommandError(f"실제 호출 실패: {type(e).__name__}: {e}") from e
        p(f"    route   {r.get('route')}")
        p(f"    소요    {ms:.0f}ms   timing={r.get('timing') or {}}")
        p(f"    근거    {len(r.get('sources') or [])}건")
        p(f"    답변    {r['answer'][:300]}")
        for pl in r.get("places") or []:
            p(f"      {pl.get('time','--:--')} [{pl['phase']:<6}] {pl['name']} ({pl['distance']}m)")
        if r.get("coursePayload"):
            cp = r["coursePayload"]
            p(f"    코스저장 payload: '{cp['title']}' · {cp['duration']} · stops {len(cp['stops'])}개")

        if o["twice"]:
            p("\n[5] 두 번째 호출 (같은 프로세스 = 실서버와 같은 웜 상태)")
            t0 = time.perf_counter()
            r2 = answer(q, stadium_name=o["stadium"])
            ms2 = (time.perf_counter() - t0) * 1000
            t1, t2 = r.get("timing") or {}, r2.get("timing") or {}
            p(f"    {'단계':<12}{'1회차(콜드)':>12}{'2회차(웜)':>12}")
            for k in ("embed_ms", "retrieval_ms", "llm_ms", "structured_ms"):
                if k in t1 or k in t2:
                    p(f"    {k:<12}{t1.get(k, 0):>10}ms{t2.get(k, 0):>10}ms")
            p(f"    {'합계':<12}{ms:>10.0f}ms{ms2:>10.0f}ms")
            p("    → 2회차가 실서버 체감 속도입니다. embed 가 여기서도 1초 넘으면 진짜 병목이에요.")
        p(ok("\n  통과 — 챗봇이 RAG 로 답하고 있습니다\n") if on else "\n")
