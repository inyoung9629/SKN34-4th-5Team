import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

class CustomUser(AbstractUser):
    """
    User 모델은 아래와 같은 필드들을 포함한다.

        username: 유저 고유 이름 (id)(unique)
        password: 유저 패스워드
        email: 이메일
        first_name: 유저 이름
        last_name: 유저 성
        is_staff: 관리자 사이트 접근 여부
        is_active: 계정 활성화 여부
        is_superuser: 슈퍼유저 여부
        last_login: 마지막 로그인 시간
        date_joined: 계정 생성 시간
    """
    birth_date = models.DateField(blank=True, null=True)
    gender = models.CharField(
		max_length=1,
		choices=[("M", "남성"), ("F", "여성")],
		null=True,
		blank=True,
	)
    nickname = models.CharField(max_length=12, blank=True)
    team_code = models.CharField(max_length=2, blank=True)
    avatar = models.TextField(blank=True)
    nickname_changed_at = models.DateTimeField(blank=True, null=True)
    notifications = models.JSONField(default=dict, blank=True)
    visibility = models.JSONField(default=dict, blank=True)


class EmailChangeChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    email = models.EmailField()
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(blank=True, null=True)


class PointWallet(models.Model):
    """서비스 공통 포인트 잔액. 모든 증감은 PointTransaction을 함께 남긴다."""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="point_wallet")
    balance = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class PointTransaction(models.Model):
    """포인트 증감 원장. source_key가 같은 지급은 한 번만 처리한다."""
    FANTASY_SETTLEMENT = "FANTASY_SETTLEMENT"
    ACTIVITY_EARNED = "ACTIVITY_EARNED"
    user = models.ForeignKey(CustomUser, on_delete=models.PROTECT, related_name="point_transactions")
    wallet = models.ForeignKey(PointWallet, on_delete=models.PROTECT, related_name="transactions")
    amount = models.BigIntegerField()
    transaction_type = models.CharField(max_length=40)
    source_type = models.CharField(max_length=80)
    source_key = models.CharField(max_length=160)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "source_type", "source_key"), name="uq_point_transaction_source")]
        ordering = ("-created_at", "-id")
