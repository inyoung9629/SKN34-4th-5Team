import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from travel.place_rag import RagUnavailable, build_index, index_path
from travel.place_rag_import import (
    DEFAULT_ANALYSIS, analysis_records, collected_documents, facility_documents,
)


class Command(BaseCommand):
    help = "수집 장소와 감사된 분석 기록으로 로컬 장소 RAG 생성 (네트워크·임베딩·DB 쓰기 없음)"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path)
        parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS)
        parser.add_argument("--without-analysis", action="store_true")

    def handle(self, *args, **options):
        try:
            places, public = collected_documents()
            facilities, facility_sources = facility_documents()
            docs = places + facilities
            analyses, experiment = ([], {}) if options["without_analysis"] else analysis_records(options["analysis_dir"], docs)
            path = options["output"] or index_path()
            report = build_index(path, docs, analyses, provenance={"public": public,
                                 "facilities": facility_sources, "analysis": experiment})
        except (OSError, ValueError, KeyError, RagUnavailable) as exc:
            raise CommandError(f"RAG 빌드 실패. 기존 인덱스는 유지됩니다: {exc}") from exc
        self.stdout.write(json.dumps({"index": str(path.resolve()), **report}, ensure_ascii=False, indent=2))
