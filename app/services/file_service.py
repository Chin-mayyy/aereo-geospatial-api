from pathlib import Path
from uuid import UUID, uuid4

from app.schemas.files import FileMetadata
from app.schemas.measurements import Measurements
from app.services.geospatial_service import DatasetError, measure_features, read_dataset


class FileStore:
    def __init__(self):
        self.records: dict[UUID, tuple[FileMetadata, Measurements]] = {}

    def process(self, filename: str, content: bytes) -> FileMetadata:
        extension = Path(filename).suffix.lower()
        if extension not in (".kml", ".zip"):
            raise DatasetError("Supported file extensions are .kml and .zip.", 415)
        if not content:
            raise DatasetError("Upload is empty.", 400)
        frame = read_dataset(content, extension)
        source_crs, measurement_crs, features = measure_features(frame)
        identifier = uuid4()
        metadata = FileMetadata(
            id=identifier, filename=filename, feature_count=len(features), crs=source_crs
        )
        measurements = Measurements(
            file_id=identifier,
            source_crs=source_crs,
            measurement_crs=measurement_crs,
            features=features,
        )
        # Publish both records together only after all processing succeeds.
        self.records[identifier] = (metadata, measurements)
        return metadata
