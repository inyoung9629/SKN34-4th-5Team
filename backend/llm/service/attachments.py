"""대화별 private 첨부. 모델 입력에만 원본을 복원하며 checkpoint 에는 ID만 남긴다."""
import base64
import io
import ipaddress
import re
import warnings
from pathlib import PurePath
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from django.http import Http404
from langchain_core.messages import HumanMessage
from rest_framework.exceptions import ValidationError

from community import image_storage
from llm.models import ChatAttachment

MAX_TEXT = 2 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class AttachmentProcessingLimit(ValueError):
    """Safe public refusal; never substitute a partially indexed source."""

    detail = "첨부 참고 자료의 전체 처리 한도(합계 2MiB)를 초과했습니다. 첨부를 줄이거나 새 대화에서 다시 시도해 주세요."

    def __init__(self):
        super().__init__(self.detail)


def reserve_attachment_deletion(key):
    from llm.models import ChatAttachmentDeletion
    ChatAttachmentDeletion.objects.get_or_create(object_key=key)


def purge_deleted_attachments(keys=None):
    import logging
    from llm.models import ChatAttachmentDeletion
    rows = ChatAttachmentDeletion.objects.all()
    if keys is not None:
        rows = rows.filter(object_key__in=keys)
    for key in rows.values_list("object_key", flat=True):
        try:
            image_storage.delete_object(key)
        except image_storage.StorageUnavailable:
            logging.getLogger(__name__).exception("attachment cleanup kept for retry")
        else:
            ChatAttachmentDeletion.objects.filter(pk=key).delete()


def reference_url(value):
    """Validate URL metadata without DNS, HTTP or extracted-source access."""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if (parts.scheme not in {"http", "https"} or not host or parts.username is not None
                or parts.password is not None or parts.port not in {None, 80, 443}
                or len(value) > 2048 or any(ord(c) <= 32 or c == "\\" for c in value)
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if "." not in host or any(not label or len(label) > 63 or not label[0].isalnum()
                                      or not label[-1].isalnum() or any(not (c.isalnum() or c == "-") for c in label)
                                      for label in host.split(".")) or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", host.rsplit(".", 1)[-1]):
                # Numeric final labels trigger legacy IPv4 parsing in browser/provider URL parsers.
                raise ValueError
        if address is not None and (not address.is_global or address.is_multicast):
            raise ValueError
        netloc = f"[{host}]" if ":" in host else host
        if parts.port and parts.port != (443 if parts.scheme == "https" else 80):
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError, TypeError) as error:
        raise ValidationError({"url": "공개 HTTP(S) URL만 사용할 수 있습니다."}) from error


def normalize_file(upload):
    name = PurePath(upload.name.replace("\\", "/")).name[:255]
    suffix = PurePath(name).suffix.lower()
    text = suffix in {".txt", ".md"}
    limit = MAX_TEXT if text else MAX_IMAGE
    raw = bytearray()
    for chunk in upload.chunks():
        raw.extend(chunk)
        if len(raw) > limit:
            raise ValidationError({"file": "파일 크기 제한을 초과했습니다."})
    if not raw:
        raise ValidationError({"file": "빈 파일은 사용할 수 없습니다."})
    if text:
        if upload.content_type not in {"text/plain", "text/markdown", "text/x-markdown"}:
            raise ValidationError({"file": "TXT/MD Content-Type이 올바르지 않습니다."})
        try:
            decoded = bytes(raw).decode("utf-8-sig")
            if any(ord(c) < 32 and c not in "\n\r\t" for c in decoded):
                raise ValueError
        except (UnicodeError, ValueError) as error:
            raise ValidationError({"file": "UTF-8 텍스트만 사용할 수 있습니다."}) from error
        return name, "text", decoded.encode("utf-8"), "text/plain", None, None
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValidationError({"file": "TXT, MD, JPEG, PNG, WebP만 사용할 수 있습니다."})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                fmt = source.format
                if (fmt not in FORMATS or FORMATS[fmt] != upload.content_type or getattr(source, "n_frames", 1) != 1
                        or max(source.size) > 10000 or source.width * source.height > 20_000_000):
                    raise ValueError
                source.load()
                image = ImageOps.exif_transpose(source)
                if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.info.clear()
                output = io.BytesIO()
                image.save(output, format=fmt)
                body = output.getvalue()
                if len(body) > MAX_IMAGE:
                    raise ValueError
                return name, "image", body, FORMATS[fmt], image.width, image.height
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise ValidationError({"file": "손상되었거나 지원하지 않는 이미지입니다."}) from error


def metadata(row):
    return {"id": str(row.id), "kind": row.kind, "name": row.name, "content_type": row.content_type,
            "size": row.size, "width": row.width, "height": row.height,
            "url": row.source_url if row.kind == "url" else f"/api/v2/chat/sessions/{row.session_id}/attachments/{row.id}/",
            "created_at": row.created_at.isoformat()}


def resolve(session_id, ids):
    ids = list(dict.fromkeys(map(str, ids)))
    if len(ids) > 10:
        raise ValidationError({"attachment_ids": "첨부 자료는 합계 10개까지 사용할 수 있습니다."})
    rows = {str(row.id): row for row in ChatAttachment.objects.filter(session_id=session_id, id__in=ids)}
    if len(rows) != len(set(map(str, ids))):
        raise Http404("attachment not found")
    ordered = [rows[str(key)] for key in ids]
    if sum(r.kind == "url" for r in ordered) > 3:
        raise ValidationError({"attachment_ids": "URL은 3개까지 사용할 수 있습니다."})
    return ordered


def read_file(row):
    from llm.service.chat_runs import check_cancelled
    check_cancelled()
    with image_storage.get_object(row.object_key) as body:
        raw = body.read((MAX_IMAGE if row.kind == "image" else MAX_TEXT) + 1)
    if len(raw) != row.size:
        raise ValueError("invalid stored attachment size")
    return raw


def multimodal(human, rows):
    images = [row for row in rows if row.kind == "image"]
    if not images:
        return human
    blocks = [{"type": "text", "text": human.text}]
    for row in images:
        blocks.append({"type": "image_url", "image_url": {"url": f"data:{row.content_type};base64,{base64.b64encode(read_file(row)).decode('ascii')}"}})
    return HumanMessage(content=blocks, id=human.id, additional_kwargs=human.additional_kwargs)


def source_text(row):
    if row.kind == "url":
        from llm.service.chat_runs import check_cancelled
        from llm.v2.agent.browser_research import web_body
        check_cancelled()
        reference_url(row.source_url)
        if row.extracted_text:
            return row.extracted_text
        result = web_body(row.source_url)
        if result.get("status") == "overflow":
            raise AttachmentProcessingLimit()
        if result.get("status") != "ok":
            raise ValueError("URL body unavailable: " + result.get("status", "error"))
        check_cancelled()
        return result["body"]
    if row.kind != "text":
        raise ValueError("only text/URL attachments have source text")
    return read_file(row).decode("utf-8")
