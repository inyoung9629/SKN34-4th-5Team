from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef

from accounts.models import CustomUser, PointTransaction
from accounts.point_policy import SIGNUP, SIGNUP_SOURCE_KEY, grant_definition
from accounts.point_service import grant_points


class Command(BaseCommand):
    help = "기존 회원에게 최초 가입 포인트를 멱등적으로 지급합니다."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="지급 대상만 출력합니다.")

    def handle(self, *args, **options):
        existing_signup_reward = PointTransaction.objects.filter(
            user_id=OuterRef("pk"),
            source_type=SIGNUP[0],
            source_key=SIGNUP_SOURCE_KEY,
        )
        users = CustomUser.objects.annotate(
            has_signup_reward=Exists(existing_signup_reward),
        ).filter(has_signup_reward=False).order_by("pk")
        if options["dry_run"]:
            self.stdout.write(f"대상 회원: {users.count()}명")
            for user in users.iterator():
                self.stdout.write(f"{user.pk}: {user.username}")
            return
        created = 0
        for user in users.iterator():
            _, _, was_created = grant_points(
                user=user,
                **grant_definition(SIGNUP, SIGNUP_SOURCE_KEY),
            )
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"가입 보상 지급 완료: {created}명"))
