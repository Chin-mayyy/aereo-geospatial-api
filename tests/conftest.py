import io
import zipfile

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def shapefile_zip(tmp_path):
    def build(geometry=None, crs="EPSG:4326", nested=True):
        directory = tmp_path / ("nested" if nested else "root")
        directory.mkdir(exist_ok=True)
        frame = gpd.GeoDataFrame(
            {"label": ["sample"]},
            geometry=[
                geometry
                if geometry is not None
                else Polygon([(77, 12), (77.001, 12), (77.001, 12.001), (77, 12.001)])
            ],
            crs=crs,
        )
        frame.to_file(directory / "sample.shp", engine="pyogrio")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for path in directory.iterdir():
                archive.write(path, ("data/" if nested else "") + path.name)
        return output.getvalue()

    return build


@pytest.fixture
def kml():
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><name>polygon</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>77,12 77.001,12 77.001,12.001 77,12.001 77,12</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>line</name><LineString><coordinates>77,12 77.001,12</coordinates>
    </LineString></Placemark><Placemark><name>point</name><Point>
    <coordinates>77,12</coordinates></Point></Placemark></Document></kml>"""
