"""외부 DB/.env/API에 접근하지 않는 백엔드 회귀 테스트 전용 설정.

SQLite syncdb로 현재 모델 계약만 검증한다. PostgreSQL 마이그레이션 검증을 대체하지 않는다.
"""
from baseball.tests.api_settings import *  # noqa: F403

ROOT_URLCONF = "llm.tests.course_urls"
INSTALLED_APPS += ["django.contrib.postgres", "llm", "travel", "community", "tving"]  # noqa: F405
MIGRATION_MODULES = {name: None for name in (
    "auth", "contenttypes", "sessions", "token_blacklist", "accounts", "baseball",
    "llm", "travel", "community", "tving",
)}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EXTERNAL_DATA_SYNC_INTERVAL_SECONDS = 600
TEST_RUNNER = "llm.tests.course_runner.CourseTestRunner"
CHAT_STREAM_IDLE_TIMEOUT_SECONDS = 5
USAGE_MAX_CALL_OUTPUT_TOKENS = 4000
