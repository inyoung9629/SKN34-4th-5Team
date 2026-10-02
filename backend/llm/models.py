import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from pgvector.django import VectorField, HnswIndex

class Document(models.Model):
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)


class DocumentChunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    content = models.TextField()
    chunk_index = models.IntegerField()
    metadata = models.JSONField(default=dict)
    embedding = VectorField(dimensions=1536)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]


# 채팅방 테이블
class ChatSession(models.Model):
    """
    NOTE: 9월 25일 비회원 로직추가 
    1. User에 Null, Black을 추가함
    2. Guest을 추가
    3. 제약조건: 둘중하나만 반드시 존재해야함.  
    """
    id = models.UUIDField(
        primary_key=True,default=uuid.uuid4,editable=False
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
        null=True,
        blank=True
    )
    guest = models.UUIDField(
        null= True,
        blank=True,
        db_index=True
    )
    title = models.CharField(max_length=255, blank=True, default="메세지 제목")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.CheckConstraint(
                # 제약조건 회원(User) or 비회원(guest) 중 하나는 반드시 존재야한다. 라는 제약조건
                condition=( 
                    Q(user__isnull=False, guest__isnull=True)
                    | Q(user__isnull=True, guest__isnull=False)
                ),
                name="chat_session_has_one_owner",
            )
        ]


class ChatThreadDeletion(models.Model):
    """삭제된 ChatSession 의 checkpoint 삭제 outbox. 세션 삭제와 같은 트랜잭션에 쓰고, checkpoint 삭제 성공 뒤 지운다.

    FK 가 아니다: 세션 행은 이미 없다. 남아 있는 행 = 아직 지우지 못한 thread (llm.service.chat_thread.purge_deleted_threads).
    """
    thread_id = models.UUIDField(primary_key=True)
    token = models.UUIDField(default=uuid.uuid4)  # 예약마다 새 값. drain 은 읽은 token 일 때만 행을 지운다
    created_at = models.DateTimeField(auto_now_add=True)


class UsageWallet(models.Model):
    """토큰 사용량 지갑. 회원(user) 또는 비회원(guest = ChatSession.guest UUID) 중 하나만 주인이다.

    period: 회원은 USAGE_TIMEZONE 기준 달("2026-09"), 비회원은 "lifetime"(충전 없음). 달이 바뀌면 used 를 0 으로 되돌린다.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="usage_wallet")
    guest = models.UUIDField(null=True, blank=True, unique=True)
    period = models.CharField(max_length=16)
    used_tokens = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False, guest__isnull=True) | Q(user__isnull=True, guest__isnull=False),
                name="usage_wallet_has_one_owner",
            ),
        ]


class UsageCharge(models.Model):
    """턴 하나의 예약·정산 원장. reserved 상태 합계가 지갑의 사용 가능량에서 빠진다."""
    RESERVED, SETTLED = "reserved", "settled"
    wallet = models.ForeignKey(UsageWallet, on_delete=models.CASCADE, related_name="charges")
    session_id = models.UUIDField(null=True)  # FK 아님: 세션이 지워져도 원장은 남는다
    period = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default=RESERVED)
    reserved_tokens = models.BigIntegerField()
    charged_tokens = models.BigIntegerField(default=0)
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    calls = models.IntegerField(default=0)
    unknown_calls = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["wallet", "status"], name="usage_charge_wallet_status")]
