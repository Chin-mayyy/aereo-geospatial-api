from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class FileMetadata(BaseModel):
    id: UUID
    filename: str
    feature_count: int
    crs: str
    status: Literal["COMPLETED"] = "COMPLETED"
