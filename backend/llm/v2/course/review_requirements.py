"""Subjective review preferences are separate from factual menu requirements."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReviewAspect = Literal["quietness", "cleanliness", "cozy", "date_friendly", "scenic_view", "walking_comfort"]
REVIEW_LABELS = {"quietness": "조용함", "cleanliness": "매장 청결도", "cozy": "아늑함",
                 "date_friendly": "데이트 분위기", "scenic_view": "경관", "walking_comfort": "걷기 편함"}
REVIEW_CATEGORIES = {"cozy": "atmosphere", "date_friendly": "suitability",
                     "scenic_view": "atmosphere", "walking_comfort": "access"}


class ReviewCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aspect: ReviewAspect
    priority: Literal["required", "preferred"] = "required"


class ReviewRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_of: list[ReviewCondition] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_aspects(self):
        if len({c.aspect for c in self.all_of}) != len(self.all_of):
            raise ValueError("후기 조건은 항목별로 한 번만 지정합니다.")
        return self

    @property
    def has_preferences(self):
        return any(c.priority == "preferred" for c in self.all_of)
