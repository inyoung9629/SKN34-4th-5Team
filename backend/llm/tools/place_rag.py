"""Read-only catalogue/keyword tool. Writes are reserved for verified ingestion."""
from pydantic import Field
from .common import ToolInput, _tool


class PlaceRagInput(ToolInput):
    query: str = Field(default="", max_length=200)
    stadium_code: str | None = None
    kind: str | None = None
    keyword_term: str = Field(default="", max_length=80)
    place_id: str | None = None


def search_place_knowledge(query="", stadium_code=None, kind=None, keyword_term="", place_id=None):
    from travel.place_rag import retrieve
    result = retrieve(query, stadium_code=stadium_code, kind=kind)
    if keyword_term:
        from travel.place_keyword_memory import retrieve_keyword_memory
        result = {**result, "keyword_memory": retrieve_keyword_memory(
            keyword_term, stadium_code=stadium_code, kind=kind, place_id=place_id)}
    return result


def create_place_rag_tool():
    return _tool(search_place_knowledge, "search_place_knowledge",
                 "저장된 장소 자료와 키워드 근거를 조회한다. 저장 여부는 추천 우선순위가 아니며 현재 조건과 출처를 확인한다.", PlaceRagInput)
