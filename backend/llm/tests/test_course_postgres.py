"""course_pg_settings에서만 실행: 실제 PostgresSaver·마이그레이션·기존 코스 기억 이관."""
import json

from django.db import connection

from llm.models import ChatSession
from llm.service.chat_thread import ChatThread, convert_legacy_threads
from llm.tests import test_course_policy
from llm.tests.test_v2_chat import CheckpointTestCase
from llm.tests.test_chat_regressions import _legacy
from llm.v2.course.runtime import previous_course_state
from llm.v2.course.state import empty_state


class PostgresCourseMemoryTest(test_course_policy.CourseMemoryTest):
    use_memory_saver = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ChatThread.setup()


class LegacyCourseMemoryTest(CheckpointTestCase):
    def test_existing_course_state_column_is_preserved_in_checkpoint_conversion(self):
        session = ChatSession.objects.create(guest="78787878-7878-7878-7878-787878787878")
        self.addCleanup(ChatThread(session.id).delete)
        ids = _legacy(session.id, ("user", "코스", "completed", []), ("assistant", "기존 답변", "completed", []))
        state = {**empty_state(), "conditions": {"team_code": "OB"}, "pending": "choice"}
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE llm_chatmessage ADD COLUMN IF NOT EXISTS course_state jsonb NULL')
            cursor.execute('UPDATE llm_chatmessage SET course_state = %s WHERE id = %s', [json.dumps(state), ids[1]])
        self.assertEqual(convert_legacy_threads(), 1)
        self.assertEqual(previous_course_state(*ChatThread(session.id).state()), state)
        self.assertEqual(convert_legacy_threads(), 0)
        # 이관은 원본 데이터를 삭제하지 않는다.
        with connection.cursor() as cursor:
            cursor.execute('SELECT course_state FROM llm_chatmessage WHERE id = %s', [ids[1]])
            raw = cursor.fetchone()[0]
        self.assertEqual(json.loads(raw) if isinstance(raw, str) else raw, state)
