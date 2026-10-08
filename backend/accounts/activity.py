"""Shared policy for authenticated member activity lookup."""

import re

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotAuthenticated, NotFound, PermissionDenied, ValidationError


def activity_visible(member):
    visibility = member.visibility
    return isinstance(visibility, dict) and visibility.get("posts") is True


def active_member(member_id):
    # Avoid a database integer overflow for a syntactically valid large ID.
    if member_id > 9_223_372_036_854_775_807:
        raise NotFound()
    return get_object_or_404(get_user_model(), pk=member_id, is_active=True)


def activity_author(request):
    raw = request.query_params.get("author_id", "")
    if not re.fullmatch(r"[1-9][0-9]*", raw) or len(raw) > 19:
        raise ValidationError({"author_id": "author_id는 양의 정수여야 합니다."})
    if not request.user.is_authenticated:
        raise NotAuthenticated()
    member = active_member(int(raw))
    if request.user.pk != member.pk and not activity_visible(member):
        raise PermissionDenied("이 회원의 글·댓글 활동 목록은 비공개입니다.")
    return member
