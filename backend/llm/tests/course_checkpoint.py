"""실제 checkpoint 그래프/저장 fence, 메모리 saver. SQLite 테스트이며 PG 검증을 대체하지 않는다."""
from contextlib import contextmanager
from unittest.mock import patch

from django.test import TestCase
from langgraph.checkpoint.memory import InMemorySaver

from llm.service.chat_thread import ChatThread, _builder
from llm.v2.course.runtime import previous_course_state


class CourseCheckpointTestCase(TestCase):
    use_memory_saver = True

    def setUp(self):
        super().setUp()
        class MemorySaver(InMemorySaver):
            def put(self, config, *args, **kwargs):
                # PostgresSaver는 생략된 namespace를 빈 문자열로 처리한다. 메모리 saver만 명시값 필요.
                config = {**config, "configurable": {"checkpoint_ns": "", **config["configurable"]}}
                return super().put(config, *args, **kwargs)

        saver = MemorySaver()
        graph = _builder.compile(checkpointer=saver)

        @contextmanager
        def memory_graph():
            yield graph

        patches = [patch("llm.service.usage.reserve", return_value=None), patch("llm.service.usage.settle")]
        if self.use_memory_saver:
            patches += [patch.object(ChatThread, "_graph", staticmethod(memory_graph)),
                        patch("llm.service.chat_thread._legacy_tables", return_value=False)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self, session):
        return ChatThread(session.id).state()

    def memory(self, session):
        return previous_course_state(*self.snapshot(session))
