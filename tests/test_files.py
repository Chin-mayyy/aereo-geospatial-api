import io
import zipfile
from uuid import uuid4

import pytest
from shapely.geometry import Point

from app.core.config import MAX_UPLOAD_BYTES


def upload(client, name, content):
    return client.post("/api/files/", files={"file": (name, content)})


def test_kml_upload_metadata_and_measurements(client, kml):
    response = upload(client, "features.kml", kml)
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert metadata["feature_count"] == 3
    assert metadata["status"] == "COMPLETED"
    assert metadata["crs"] == "EPSG:4326"
    assert client.get(f"/api/files/{metadata['id']}/").json() == metadata
    data = client.get(f"/api/files/{metadata['id']}/measurements/").json()
    features = {feature["geometry_type"]: feature for feature in data["features"]}
    assert features["Polygon"]["measurement"] == pytest.approx(12050, rel=0.02)
    assert features["LineString"]["measurement"] == pytest.approx(109, rel=0.02)
    assert features["Point"]["measurement"] is None
    assert features["Point"]["properties"]["Name"] == "point"


def test_nested_shapefile_upload(client, shapefile_zip):
    response = upload(client, "dataset.zip", shapefile_zip())
    assert response.status_code == 201, response.text
    data = client.get(f"/api/files/{response.json()['id']}/measurements/").json()
    feature = data["features"][0]
    assert feature["properties"] == {"label": "sample"}
    assert feature["geometry"]["coordinates"][0][0] == [77, 12]
    assert feature["crs"] == "EPSG:4326"
    assert data["measurement_crs"] == "EPSG:32643"
    assert feature["measurement_unit"] == "m²"
    assert feature["measurement"] > 10000


@pytest.mark.parametrize(
    "name,content,status",
    [
        ("bad.txt", b"hello", 415),
        ("empty.kml", b"", 400),
        ("bad.kml", b"not xml", 422),
        ("bad.zip", b"not a zip", 422),
        ("evil.kml", b"<!DOCTYPE kml><kml/>", 422),
    ],
)
def test_invalid_upload_is_atomic(client, name, content, status):
    assert upload(client, name, content).status_code == status
    assert client.app.state.file_store.records == {}


@pytest.mark.parametrize(
    "member", ["readme.txt", "../escape.shp", "/escape.shp", "C:/escape.shp", "data\\escape.shp"]
)
def test_invalid_archive(client, member):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, "fake")
    assert upload(client, "bad.zip", buffer.getvalue()).status_code == 422
    assert client.app.state.file_store.records == {}


def test_missing_crs(client, shapefile_zip):
    response = upload(client, "no-crs.zip", shapefile_zip(Point(77, 12), crs=None))
    assert response.status_code == 422
    assert "CRS" in response.json()["detail"]
    assert client.app.state.file_store.records == {}


@pytest.mark.parametrize("suffix", ["", "measurements/"])
def test_unknown_id(client, suffix):
    assert client.get(f"/api/files/{uuid4()}/{suffix}").status_code == 404


def test_upload_size_limit(client):
    assert upload(client, "large.kml", b"x" * (MAX_UPLOAD_BYTES + 1)).status_code == 413


def test_distinct_upload_ids(client, kml):
    first = upload(client, "same.kml", kml).json()
    second = upload(client, "same.kml", kml).json()
    assert first["id"] != second["id"]


def test_multiple_kml_layers(client):
    content = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Folder><name>one</name><Placemark><Point><coordinates>77,12</coordinates></Point>
    </Placemark></Folder><Folder><name>two</name><Placemark><Point>
    <coordinates>77.1,12.1</coordinates></Point></Placemark></Folder></Document></kml>"""
    response = upload(client, "layers.kml", content)
    assert response.status_code == 201, response.text
    assert response.json()["feature_count"] == 2


def test_request_size_limit_before_parsing(client):
    from app.core.upload_limit import MAX_REQUEST_BYTES

    response = upload(client, "large.kml", b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert client.app.state.file_store.records == {}


def test_geometry_only_kml(client):
    content = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>
    <Point><coordinates>77,12</coordinates></Point></Placemark></kml>"""
    assert upload(client, "point.kml", content).status_code == 201


def test_utf16_dtd_rejected(client):
    content = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE kml><kml/>'.encode("utf-16")
    assert upload(client, "evil.kml", content).status_code == 422


def test_api_mixed_unsupported_geometry(client):
    content = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><MultiGeometry><Point><coordinates>77,12</coordinates></Point>
    <LineString><coordinates>77,12 77.001,12</coordinates></LineString></MultiGeometry>
    </Placemark><Placemark><LineString><coordinates>77,12 77.001,12</coordinates>
    </LineString></Placemark></Document></kml>"""
    response = upload(client, "mixed.kml", content)
    assert response.status_code == 201, response.text
    features = client.get(f"/api/files/{response.json()['id']}/measurements/").json()["features"]
    assert features[0]["geometry_type"] == "GeometryCollection"
    assert features[0]["measurement_status"] == "UNSUPPORTED_GEOMETRY"
    assert features[0]["geometry"]["geometries"]
    assert features[1]["measurement_status"] == "MEASURED"


def test_unusable_prj_rejected(client, shapefile_zip):
    original = io.BytesIO(shapefile_zip())
    rewritten = io.BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(rewritten, "w") as target:
        for member in source.infolist():
            target.writestr(
                member.filename,
                b"invalid CRS" if member.filename.endswith(".prj") else source.read(member),
            )
    assert upload(client, "bad-crs.zip", rewritten.getvalue()).status_code == 422
    assert client.app.state.file_store.records == {}
