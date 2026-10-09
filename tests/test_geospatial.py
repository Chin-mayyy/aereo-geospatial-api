import io
import stat
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import Point

from app.services.geospatial_service import (
    DatasetError,
    extract_shapefile,
    measure_features,
    read_dataset,
)


def zip_entries(entries):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return content.getvalue()


def test_archive_symlink_rejected(tmp_path):
    member = zipfile.ZipInfo("link")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(DatasetError, match="unsafe"):
        extract_shapefile(zip_entries([(member, "../outside")]), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_archive_duplicate_rejected(tmp_path):
    with pytest.raises(DatasetError, match="duplicate"):
        extract_shapefile(zip_entries([("data.shp", ""), ("DATA.SHP", "")]), tmp_path)


def test_missing_sidecars_rejected(tmp_path):
    with pytest.raises(DatasetError, match=".shx"):
        extract_shapefile(zip_entries([("data.shp", "")]), tmp_path)


def test_multiple_datasets_rejected(tmp_path):
    with pytest.raises(DatasetError, match="exactly one"):
        extract_shapefile(zip_entries([("one.shp", ""), ("two.shp", "")]), tmp_path)


def test_member_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.geospatial_service.MAX_ARCHIVE_MEMBERS", 1)
    with pytest.raises(DatasetError, match="too many"):
        extract_shapefile(zip_entries([("one", ""), ("two", "")]), tmp_path)


def test_expanded_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.geospatial_service.MAX_EXTRACTED_BYTES", 1)
    with pytest.raises(DatasetError) as error:
        extract_shapefile(zip_entries([("one", "large")]), tmp_path)
    assert error.value.status_code == 413


def test_unavailable_kml_driver(kml, monkeypatch):
    monkeypatch.setattr("app.services.geospatial_service.pyogrio.list_drivers", lambda **_: {})
    with pytest.raises(DatasetError, match="GDAL") as error:
        read_dataset(kml, ".kml")
    assert error.value.status_code == 503


def test_feature_limit(kml, monkeypatch):
    monkeypatch.setattr("app.services.geospatial_service.MAX_FEATURES", 1)
    with pytest.raises(DatasetError, match="feature limit"):
        read_dataset(kml, ".kml")


def test_temporary_directory_cleaned(kml, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    read_dataset(kml, ".kml")
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(DatasetError):
        read_dataset(b"bad", ".zip")
    assert list(tmp_path.iterdir()) == []


def test_nonfinite_geometry_rejected():
    frame = gpd.GeoDataFrame(geometry=[Point(float("inf"), 12)], crs=4326)
    with pytest.raises(DatasetError, match="non-finite"):
        measure_features(frame)
