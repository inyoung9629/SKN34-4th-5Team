from rest_framework import serializers

from llm.models import AnswerFeedback
from llm.serializer.message import MessageIdField

REASONS = ("incorrect", "irrelevant", "incomplete", "other")


class FeedbackInputSerializer(serializers.Serializer):
    message_id = MessageIdField()
    rating = serializers.ChoiceField(choices=("up", "down"), allow_null=True)
    reason = serializers.ChoiceField(choices=("", *REASONS), default="")
    comment = serializers.CharField(max_length=1000, allow_blank=True, default="")

    def validate_comment(self, value):
        if not isinstance(self.initial_data.get("comment", ""), str):
            raise serializers.ValidationError("의견은 문자열이어야 합니다.")
        return value

    def validate(self, data):
        if data["rating"] != "down" and (data["reason"] or data["comment"]):
            raise serializers.ValidationError("아쉬워요 평가에만 사유와 의견을 남길 수 있습니다.")
        return data


def public_feedback(row):
    return {"rating": row.rating, "reason": row.reason, "comment": row.comment} if row else None


class AdminFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerFeedback
        fields = ("id", "session_id", "answer_id", "message_id", "rating", "reason", "comment",
                  "question", "answer", "metadata", "created_at", "updated_at")
        read_only_fields = fields
