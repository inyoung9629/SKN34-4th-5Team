"""서비스 활동별 포인트 지급 기준을 한 곳에서 관리한다."""

SIGNUP = ("signup", 100, "회원가입 보상")
SIGNUP_SOURCE_KEY = "initial"
DAILY_LOGIN = ("daily_login", 10, "오늘의 첫 로그인 보상")
POST = ("post", 1, "게시글 작성 보상")
POST_READ = ("post_read", 1, "게시글 최초 읽기 보상")
COMMENT = ("comment", 1, "댓글 작성 보상")


def grant_definition(rule, source_key):
    source_type, amount, description = rule
    return {"amount": amount, "source_type": source_type, "source_key": source_key, "description": description}
