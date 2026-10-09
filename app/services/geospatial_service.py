import io
import json
import math
import stat
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
import pyogrio
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely import transform
from shapely.errors import GEOSException
from shapely.geometry import mapping

from app.core.config import MAX_ARCHIVE_MEMBERS, MAX_EXTRACTED_BYTES, MAX_FEATURES
from app.schemas.measurements import FeatureMeasurement


class DatasetError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def extract_shapefile(content: bytes, directory: Path) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise DatasetError("Archive contains too many entries.")
            if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
                raise DatasetError("Expanded archive exceeds the size limit.", 413)
            seen = set()
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or ":" in name
                    or stat.S_ISLNK(mode)
                    or member.flag_bits & 1
                    or name.casefold() in seen
                ):
                    raise DatasetError("Archive contains unsafe or duplicate entries.")
                seen.add(name.casefold())
            archive.extractall(directory)
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as error:
        raise DatasetError("Invalid or unreadable ZIP archive.") from error
    shapefiles = [path for path in directory.rglob("*") if path.suffix.lower() == ".shp"]
    if len(shapefiles) != 1:
        raise DatasetError("Archive must contain exactly one Shapefile dataset.")
    shapefile = shapefiles[0]
    sidecars = {path.name.lower() for path in shapefile.parent.iterdir()}
    if any(shapefile.stem.lower() + suffix not in sidecars for suffix in (".shx", ".dbf")):
        raise DatasetError("Shapefile requires matching .shx and .dbf files.")
    # GDAL resolves sidecars by filename; normalize extensions and stems consistently.
    for path in list(shapefile.parent.iterdir()):
        if path.is_file() and path.stem.lower() == shapefile.stem.lower():
            normalized = path.with_name(shapefile.stem + path.suffix.lower())
            if path != normalized:
                path.rename(normalized)
    return shapefile.with_suffix(".shp")


def read_dataset(content: bytes, extension: str) -> gpd.GeoDataFrame:
    with TemporaryDirectory(prefix="aereo-") as temporary:
        directory = Path(temporary)
        if extension == ".zip":
            path = extract_shapefile(content, directory)
        else:
            drivers = pyogrio.list_drivers(read=True)
            if "KML" not in drivers and "LIBKML" not in drivers:
                raise DatasetError("KML reading requires a GDAL KML or LIBKML driver.", 503)
            # Do not pass external entities or network references to GDAL's XML reader.
            try:
                root = ElementTree.fromstring(content, forbid_dtd=True)
            except (ElementTree.ParseError, DefusedXmlException) as error:
                raise DatasetError(
                    "KML must be valid XML without DTDs or external entities."
                ) from error
            if root.tag.rsplit("}", 1)[-1] != "kml":
                raise DatasetError("Upload is not a KML document.")
            if any(element.tag.rsplit("}", 1)[-1] == "NetworkLink" for element in root.iter()):
                raise DatasetError("KML network links are not supported.")
            path = directory / "upload.kml"
            path.write_bytes(content)
        try:
            layers = pyogrio.list_layers(path)
            frames = []
            count = 0
            for layer_name, geometry_type in layers:
                if geometry_type is None:
                    continue
                frame = gpd.read_file(
                    path, layer=layer_name, engine="pyogrio", max_features=MAX_FEATURES + 1
                )
                count += len(frame)
                if count > MAX_FEATURES:
                    raise DatasetError("Dataset exceeds the feature limit.", 413)
                frames.append(frame)
            if not frames or count == 0:
                raise DatasetError("Dataset contains no features.")
            if any(frame.crs is None for frame in frames):
                raise DatasetError("Source CRS is missing; provide a valid .prj file.")
            if any(frame.crs != frames[0].crs for frame in frames):
                raise DatasetError("Dataset layers must share a source CRS.")
            return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
        except DatasetError:
            raise
        except Exception as error:
            raise DatasetError(
                "Dataset cannot be read; check its geometry and CRS metadata."
            ) from error


def select_measurement_crs(frame: gpd.GeoDataFrame) -> CRS | None:
    usable = frame.geometry[
        [geometry is not None and not geometry.is_empty for geometry in frame.geometry]
    ]
    if usable.empty:
        return None
    try:
        geographic = usable.to_crs(4326)
        west, south, east, north = geographic.total_bounds
        if not all(math.isfinite(value) for value in (west, south, east, north)):
            return None
        # Conservative local UTM coverage; refuse wide, polar, or dateline-spanning data.
        if west < -180 or east > 180 or south < -80 or north > 84:
            return None
        if east - west > 6 or north - south > 8 or south < 0 < north:
            return None
        selected = geographic.estimate_utm_crs()
        if selected is None:
            return None
        bounds = selected.area_of_use.bounds
        if west < bounds[0] or east > bounds[2] or south < bounds[1] or north > bounds[3]:
            return None
        return selected
    except (ValueError, RuntimeError, ProjError, GEOSException):
        return None


def measure_features(frame: gpd.GeoDataFrame) -> tuple[str, str | None, list[FeatureMeasurement]]:
    source_crs = CRS(frame.crs).to_string()
    measurement_crs = select_measurement_crs(frame)
    transformer = (
        Transformer.from_crs(frame.crs, measurement_crs, always_xy=True)
        if measurement_crs
        else None
    )
    # Pandas handles timestamps, numpy scalars, and missing attributes as JSON values.
    attribute_frame = frame.drop(columns=frame.geometry.name)
    attributes = (
        json.loads(attribute_frame.to_json(orient="records", date_format="iso"))
        if len(attribute_frame.columns)
        else [{} for _ in range(len(frame))]
    )
    features = []
    for index, geometry in enumerate(frame.geometry):
        geometry_json = None
        status = "MISSING_GEOMETRY"
        value = unit = None
        if geometry is not None:
            geometry_json = mapping(geometry)
            try:
                json.dumps(geometry_json, allow_nan=False)
            except ValueError:
                raise DatasetError("Geometry contains non-finite coordinates.") from None
            if geometry.is_empty:
                status = "EMPTY_GEOMETRY"
            elif not geometry.is_valid:
                status = "INVALID_GEOMETRY"
            elif geometry.geom_type in ("Point", "MultiPoint"):
                status = "NOT_APPLICABLE"
            elif geometry.geom_type not in (
                "Polygon",
                "MultiPolygon",
                "LineString",
                "MultiLineString",
            ):
                status = "UNSUPPORTED_GEOMETRY"
            elif transformer is None:
                status = "UNSUITABLE_EXTENT"
            else:
                try:
                    projected = transform(
                        geometry, transformer.transform, interleaved=False, include_z=geometry.has_z
                    )
                    polygon = geometry.geom_type in ("Polygon", "MultiPolygon")
                    value = projected.area if polygon else projected.length
                    if not projected.is_valid or not math.isfinite(value):
                        raise ValueError("Unusable projected geometry")
                    unit = "m²" if polygon else "m"
                    status = "MEASURED"
                except (ValueError, RuntimeError, ProjError, GEOSException):
                    value = unit = None
                    status = "TRANSFORMATION_FAILED"
        features.append(
            FeatureMeasurement(
                feature_id=index,
                geometry_type=geometry.geom_type if geometry is not None else None,
                geometry=geometry_json,
                crs=source_crs,
                properties=attributes[index],
                measurement=value,
                measurement_unit=unit,
                measurement_status=status,
            )
        )
    return source_crs, measurement_crs.to_string() if measurement_crs else None, features
