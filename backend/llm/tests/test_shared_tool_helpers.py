"""tools/common.py 공유 헬퍼의 회귀 테스트.

baseball/stadium/community/travel/weather 도메인 파일이 예전에 각자 들고 있던
_json/_rows/_result/_safe/_tool/ToolInput/LimitInput 이 하나로 합쳐진 뒤에도
직렬화, 에러 메시지, 스키마 동작이 그대로인지 확인한다. 실제 모델/임베딩
호출이나 DB 접근 없이 순수 헬퍼 동작만 검증한다.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import DatabaseError
from django.test import SimpleTestCase
from langchain_core.tools import ToolException
from pydantic import ValidationError

from ..tools.common import DB_ERROR, INVALID_INPUT, LimitInput, ToolInput, _json, _result, _rows, _safe, _tool


class FakeQuerySet:
    """.values(*fields)[:limit] 체인만 필요한 최소 스텁."""

    def __init__(self, rows):
        self._rows = rows

    def values(self, *fields):
        return [{field: row[field] for field in fields} for row in self._rows]


class JsonHelperTest(SimpleTestCase):
    def test_uuid_and_decimal_become_strings(self):
        value = UUID("12345678-1234-4123-8123-123456789abc")
        self.assertEqual(_json(value), str(value))
        self.assertEqual(_json(Decimal("1.50")), "1.50")

    def test_date_like_values_use_isoformat(self):
        self.assertEqual(_json(date(2026, 9, 27)), "2026-09-27")

    def test_plain_values_pass_through_unchanged(self):
        self.assertEqual(_json("서울"), "서울")
        self.assertEqual(_json(None), None)
        self.assertEqual(_json(3), 3)


class RowsHelperTest(SimpleTestCase):
    def test_rows_projects_fields_and_applies_limit_and_json(self):
        queryset = FakeQuerySet([
            {"id": 1, "code": "LG", "at": date(2026, 9, 1)},
            {"id": 2, "code": "OB", "at": date(2026, 9, 2)},
            {"id": 3, "code": "SS", "at": date(2026, 9, 3)},
        ])
        rows = _rows(queryset, ("id", "code", "at"), 2)
        self.assertEqual(rows, [
            {"id": 1, "code": "LG", "at": "2026-09-01"},
            {"id": 2, "code": "OB", "at": "2026-09-02"},
        ])


class ResultHelperTest(SimpleTestCase):
    def test_result_adds_count_and_keeps_metadata(self):
        payload = _result([{"a": 1}, {"a": 2}], stale=False, warning=None)
        self.assertEqual(payload, {"stale": False, "warning": None, "count": 2, "items": [{"a": 1}, {"a": 2}]})


class SafeDecoratorTest(SimpleTestCase):
    def test_database_error_becomes_tool_exception_with_shared_message(self):
        @_safe
        def boom():
            raise DatabaseError("connection lost")

        with self.assertRaises(ToolException) as ctx:
            boom()
        self.assertEqual(str(ctx.exception), DB_ERROR)

    def test_tool_exception_passes_through_unchanged(self):
        @_safe
        def boom():
            raise ToolException("이미 도구 예외")

        with self.assertRaises(ToolException) as ctx:
            boom()
        self.assertEqual(str(ctx.exception), "이미 도구 예외")

    def test_wrapped_function_preserves_metadata_and_return_value(self):
        def sample(x, y=1):
            """설명."""
            return x + y

        wrapped = _safe(sample)
        self.assertEqual(wrapped.__name__, "sample")
        self.assertEqual(wrapped.__doc__, "설명.")
        self.assertEqual(wrapped(2, y=3), 5)


class ToolBuilderTest(SimpleTestCase):
    def test_tool_wires_name_schema_and_shared_invalid_input_message(self):
        def echo(limit=20):
            return {"limit": limit}

        built = _tool(echo, "echo_tool", "설명", LimitInput)
        self.assertEqual(built.name, "echo_tool")
        self.assertEqual(built.description, "설명")
        self.assertIs(built.args_schema, LimitInput)
        self.assertEqual(built.invoke({"limit": 5}), {"limit": 5})

    def test_tool_reports_shared_invalid_input_message_on_bad_args(self):
        def echo(limit=20):
            return {"limit": limit}

        built = _tool(echo, "echo_tool", "설명", LimitInput)
        self.assertEqual(built.invoke({"limit": "not-a-number"}), INVALID_INPUT)


class SchemaBaseClassesTest(SimpleTestCase):
    def test_tool_input_strips_whitespace(self):
        class Sample(ToolInput):
            name: str

        self.assertEqual(Sample(name="  잠실  ").name, "잠실")

    def test_limit_input_default_and_bounds(self):
        self.assertEqual(LimitInput().limit, 20)
        with self.assertRaises(ValidationError):
            LimitInput(limit=0)
        with self.assertRaises(ValidationError):
            LimitInput(limit=101)
