"""Docker 로컬 db 전용 독립 테스트 DB. 실제 서비스 DB 이름은 사용하지 않는다."""
import os
from config.settings import *  # noqa: F403

_test_name = os.environ["COURSE_TEST_DB_NAME"]
if not _test_name.startswith("test_course_merge_") or not _test_name.replace("_", "").isalnum():
    raise ValueError("명시적인 test_course_merge_* 임시 DB 이름이 필요합니다")
DATABASES = {"default": {**DATABASES["default"], "HOST": "db", "PORT": "5432", "NAME": "postgres",
                         "TEST": {"NAME": _test_name}}}  # noqa: F405
DATABASE_ROUTERS = []
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
CHAT_STREAM_IDLE_TIMEOUT_SECONDS = 5
