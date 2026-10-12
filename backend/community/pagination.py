from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError as PydanticValidationError,
    field_validator,
)
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

from .serializers import CommunityPostSerializer
from .models import TEAM_CATEGORIES


class CommunityPostQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int | None = Field(default=None, le=2_147_483_647)
    page_size: int | None = Field(default=None, le=100)
    q: str | None = Field(default=None, max_length=200)
    search_field: Literal["all", "title", "author"] = "all"
    category: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        if value not in TEAM_CATEGORIES:
            raise ValueError("올바른 글 분류를 입력해 주세요.")
        return value

    @field_validator("page", "page_size", mode="before")
    @classmethod
    def parse_positive_integer(cls, value):
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or value.startswith("0"):
            raise ValueError("양의 정수를 입력해 주세요.")
        return int(value)

    @field_validator("q", mode="before")
    @classmethod
    def trim_query(cls, value):
        return value.strip() if isinstance(value, str) else value

    @classmethod
    def from_params(cls, params):
        values = {name: params.get(name) for name in cls.model_fields if name in params}
        try:
            return cls.model_validate(values)
        except PydanticValidationError as exc:
            messages = {
                "page": "page는 1 이상 2147483647 이하의 정수여야 합니다.",
                "page_size": "page_size는 1 이상 100 이하의 정수여야 합니다.",
                "q": "q는 공백을 제외하고 200자 이하여야 합니다.",
                "category": "올바른 글 분류를 입력해 주세요.",
                "search_field": "search_field는 all, title, author 중 하나여야 합니다.",
            }
            errors = {str(error["loc"][0]): messages[str(error["loc"][0])] for error in exc.errors()}
            raise ValidationError(errors) from exc


class PublicPageNumberPagination(PageNumberPagination):
    @staticmethod
    def public_link(link):
        if link is None:
            return None
        parsed = urlsplit(link)
        path = parsed.path if parsed.path.startswith("/api/v1/") else f"/api/v1{parsed.path}"
        return urlunsplit(("", "", path, parsed.query, ""))

    def get_next_link(self):
        return self.public_link(super().get_next_link())

    def get_previous_link(self):
        return self.public_link(super().get_previous_link())


class CommunityPostPagination(PublicPageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        if not any(key in request.query_params for key in ("page", "page_size", "author_id")):
            return None
        return super().paginate_queryset(queryset, request, view)


class CommunityPostPageSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = CommunityPostSerializer(many=True)
