from unittest.mock import patch

from django.db.models import Index
from django.test.runner import DiscoverRunner
from pgvector.django import HnswIndex


class CourseTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        # SQLite에는 HNSW가 없으므로 인덱스 생성만 일반 인덱스로 대체한다.
        # 코스 정책/채팅 테스트는 벡터 검색을 호출하지 않는다. 실제 PG 검증과 명확히 분리.
        with patch.object(HnswIndex, "create_sql", Index.create_sql):
            return super().setup_databases(**kwargs)
