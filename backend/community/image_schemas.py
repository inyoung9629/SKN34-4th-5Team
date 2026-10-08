from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ImageMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    content_type: Literal["image/jpeg", "image/png", "image/webp"] = Field(alias="contentType")
    size: int = Field(gt=0, le=5 * 1024 * 1024)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: datetime = Field(alias="createdAt")


def image_metadata(image):
    data = ImageMetadata.model_validate(image).model_dump(by_alias=True, mode="json")
    data["url"] = f"/api/v1/community/images/{image.pk}/"
    return data
