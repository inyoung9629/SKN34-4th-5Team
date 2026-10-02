from django.db import migrations


class Migration(migrations.Migration):
    """로컬에서 이미 적용된 이력의 호환 표식.

    기존 DB의 course_state 열/행은 보존하고 checkpoint 이관기가 읽는다.
    새 설치에는 삭제된 ORM 모델의 필드를 다시 만들지 않는다.
    """
    dependencies = [("llm", "0006_chattoolcall_and_more")]
    operations = []
