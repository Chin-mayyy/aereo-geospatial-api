from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class FeatureMeasurement(BaseModel):
    feature_id: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str
    properties: dict[str, Any]
    measurement: float | None = None
    measurement_unit: Literal["m²", "m"] | None = None
    measurement_status: str


class Measurements(BaseModel):
    file_id: UUID
    source_crs: str
    measurement_crs: str | None
    features: list[FeatureMeasurement]
