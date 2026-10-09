import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)

from app.services.geospatial_service import measure_features, select_measurement_crs


def test_mixed_features_preserved():
    polygon = Polygon([(77, 12), (77.001, 12), (77.001, 12.001), (77, 12.001)])
    line = LineString([(77, 12), (77.001, 12)])
    geometries = [
        polygon,
        line,
        Point(77, 12),
        GeometryCollection([Point(77, 12)]),
        MultiPolygon([polygon]),
        MultiLineString([line]),
        None,
        Polygon([(77, 12), (77.001, 12.001), (77, 12.001), (77.001, 12)]),
    ]
    frame = gpd.GeoDataFrame({"value": [None] * len(geometries)}, geometry=geometries, crs=4326)
    source, target, features = measure_features(frame)
    assert source == "EPSG:4326"
    assert target == "EPSG:32643"
    geodesic_area, _ = Geod(ellps="WGS84").geometry_area_perimeter(polygon)
    assert features[0].measurement == pytest.approx(abs(geodesic_area), rel=0.002)
    _, _, geodesic_length = Geod(ellps="WGS84").inv(77, 12, 77.001, 12)
    assert features[1].measurement == pytest.approx(geodesic_length, rel=0.002)
    assert features[0].measurement > polygon.area * 1e9
    assert features[1].measurement > line.length * 1e4
    assert features[2].measurement_status == "NOT_APPLICABLE"
    assert features[3].measurement_status == "UNSUPPORTED_GEOMETRY"
    assert features[4].measurement == pytest.approx(features[0].measurement)
    assert features[5].measurement == pytest.approx(features[1].measurement)
    assert features[6].measurement_status == "MISSING_GEOMETRY"
    assert features[7].measurement_status == "INVALID_GEOMETRY"
    assert features[0].properties == {"value": None}
    assert list(features[1].geometry["coordinates"][0]) == [77, 12]


def test_projected_source_is_preserved_and_reprojected():
    frame = gpd.GeoDataFrame(geometry=[LineString([(77, 12), (77.001, 12)])], crs=4326).to_crs(3857)
    source, target, features = measure_features(frame)
    assert source == "EPSG:3857"
    assert target == "EPSG:32643"
    assert features[0].measurement == pytest.approx(109, rel=0.02)
    assert features[0].geometry["coordinates"][0][0] > 8_000_000


@pytest.mark.parametrize(
    "geometry",
    [
        LineString([(-20, 0), (20, 0)]),
        LineString([(179, 10), (-179, 10)]),
        LineString([(0, 85), (1, 85)]),
        LineString([(5.9, 10), (6.1, 10)]),
    ],
)
def test_unsuitable_extent_is_explicit(geometry):
    frame = gpd.GeoDataFrame(geometry=[geometry], crs=4326)
    _, target, features = measure_features(frame)
    assert target is None
    assert features[0].measurement is None
    assert features[0].measurement_status == "UNSUITABLE_EXTENT"


def test_southern_hemisphere():
    frame = gpd.GeoDataFrame(geometry=[LineString([(18, -33), (18.001, -33)])], crs=4326)
    assert select_measurement_crs(frame).to_epsg() == 32734


def test_empty_geometry():
    frame = gpd.GeoDataFrame(geometry=[Point()], crs=4326)
    assert measure_features(frame)[2][0].measurement_status == "EMPTY_GEOMETRY"
