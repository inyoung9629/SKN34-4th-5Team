"""Food intent only; model knowledge and catalogue categories are not menu evidence."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class FoodCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cuisine", "menu"]
    name: Term
    # Properties of THIS dish, not exclusions against every menu of the restaurant.
    qualifiers: list[Term] = Field(default_factory=list, max_length=4)
    exclude: bool = Field(default=False, strict=True)


class FoodAlternatives(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any_of: list[FoodCondition] = Field(min_length=1, max_length=4)


class FoodRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_of: list[FoodAlternatives] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded_conditions(self):
        if sum(len(group.any_of) for group in self.all_of) > 8:
            raise ValueError("음식 세부 조건은 최대 8개입니다.")
        return self

    def conditions(self):
        return {f"{i}.{j}": term for i, group in enumerate(self.all_of)
                for j, term in enumerate(group.any_of)}

    def evaluate(self, verdicts):
        """Three-valued AND of ORs. A missing fact can never satisfy a hard condition."""
        groups = []
        for i, group in enumerate(self.all_of):
            values = [verdicts.get(f"{i}.{j}", "unknown") for j in range(len(group.any_of))]
            groups.append("pass" if "pass" in values else "fail" if all(v == "fail" for v in values) else "unknown")
        return "fail" if "fail" in groups else "pass" if all(v == "pass" for v in groups) else "unknown"


def condition_label(term):
    label = term.name + (" (" + ", ".join(term.qualifiers) + ")" if term.qualifiers else "")
    return label + (" 제외" if term.exclude else "")
