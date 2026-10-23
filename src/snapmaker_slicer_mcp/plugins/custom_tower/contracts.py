"""Finite inputs for the optional, role-based native tower generator."""
from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Tool = Annotated[int, Field(strict=True, ge=0, le=3)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class HybridOptions(BaseModel):
    """Endpoints receive anonymous candidate summaries, never models or G-code."""
    model_config = ConfigDict(extra='forbid')
    ranker_url: str | None = None
    llm_url: str | None = None
    ranker_model: str | None = None
    llm_model: str | None = None
    timeout_seconds: float = Field(default=10, gt=0, le=30, allow_inf_nan=False)

    @field_validator('ranker_url', 'llm_url')
    @classmethod
    def endpoint(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if (url.scheme not in {'http', 'https'} or not url.hostname or url.username
                or url.password or url.fragment or url.query or any(c.isspace() for c in value)):
            raise ValueError('Use an explicit HTTP(S) endpoint without credentials/query/fragment')
        return value

    @model_validator(mode='after')
    def model_required(self):
        if self.llm_url and not self.llm_model:
            raise ValueError('llm_model is required with llm_url')
        return self


class TowerOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_tools: list[Tool] = Field(min_length=1, max_length=3)
    interface_tool: Tool
    primary_object_id: int = Field(strict=True, gt=0)
    minimum_width_mm: float = Field(default=28, ge=12, le=50, allow_inf_nan=False)
    maximum_width_mm: float = Field(default=56, ge=24, le=100, allow_inf_nan=False)
    brim_mm: float = Field(default=6, ge=0, le=20, allow_inf_nan=False)
    center_xy: tuple[float, float] | None = None
    clearance_mm: float = Field(default=2, ge=1, le=20, allow_inf_nan=False)
    line_width_mm: float = Field(default=.5, ge=.3, le=.8, allow_inf_nan=False)
    structural_infill_percent: int = Field(default=10, strict=True, ge=5, le=25)
    speed_mm_s: float = Field(default=40, ge=5, le=100, allow_inf_nan=False)
    purge_volume_mm3: dict[Tool, Positive] = Field(default_factory=dict)
    max_iterations: int = Field(default=3, strict=True, ge=1, le=4)
    # An explicit exception for a cylindrical/constant model. Never inferred.
    foundation_height_mm: float | None = Field(default=None, gt=0, le=50, allow_inf_nan=False)
    demo_object_ids: list[int] = Field(default_factory=list)
    demo_source_plate: int | None = Field(default=None, strict=True, ge=2)
    legacy_tower_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)
    hybrid: HybridOptions | None = None

    @field_validator('purge_volume_mm3', mode='before')
    @classmethod
    def json_keys(cls, value):
        if isinstance(value,dict):
            return {int(k) if isinstance(k,str) and k in {'0','1','2','3'} else k:v for k,v in value.items()}
        return value

    @model_validator(mode='after')
    def roles_and_dimensions(self):
        if len(set(self.model_tools)) != len(self.model_tools) or self.interface_tool in self.model_tools:
            raise ValueError('Use distinct model tools and a separate interface tool')
        if abs(self.maximum_width_mm - 2 * self.minimum_width_mm) > 1e-6:
            raise ValueError('Foundation width must be twice the final width; brim is additional')
        if set(self.purge_volume_mm3) - set(self.model_tools + [self.interface_tool]):
            raise ValueError('Purge volumes must reference declared physical tools')
        if any(v > 200 for v in self.purge_volume_mm3.values()):
            raise ValueError('Purge volume exceeds the supported 200 mm3 bound')
        if self.center_xy and any(not float('-inf') < v < float('inf') for v in self.center_xy):
            raise ValueError('center_xy must be finite')
        if len(set(self.demo_object_ids)) != len(self.demo_object_ids):
            raise ValueError('Demo IDs must be unique')
        if self.demo_object_ids and self.demo_source_plate is None:
            raise ValueError('Declare demo_source_plate for the largest assembled reference')
        if (len(set(self.legacy_tower_ids))!=len(self.legacy_tower_ids)
                or self.primary_object_id in self.legacy_tower_ids
                or set(self.demo_object_ids)&set(self.legacy_tower_ids)):
            raise ValueError('Legacy auxiliary IDs must be unique and distinct from model/demo IDs')
        return self

    def target(self, tool):
        # A comparison budget, never an assertion of physical cleaning quality.
        return self.purge_volume_mm3.get(tool, 12.)
