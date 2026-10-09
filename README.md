# AEREO Geospatial File Measurement API

A small FastAPI backend for the AEREO SDE intern assignment. Upload KML or a ZIP containing one ESRI Shapefile, then retrieve metadata and feature measurements. Geometry and attributes remain associated with the uploaded file's UUID.

## Setup and commands

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Development and verification used Python 3.12.14. Binary GeoPandas/Pyogrio/Shapely/PyProj wheels supply the geospatial runtime on supported platforms. Platforms without compatible wheels may require system GDAL, GEOS and PROJ installations.

From this directory:

```bash
uv sync --python 3.12 --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use a single worker. Interactive API documentation is at `http://127.0.0.1:8000/docs`.

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

`uv.lock` pins the resolved dependency versions. No external dataset, database or service is required for the tests. To inspect your GDAL KML capabilities:

```bash
uv run python -c "import pyogrio; print(pyogrio.__gdal_version_string__); print({k:v for k,v in pyogrio.list_drivers(read=True).items() if 'KML' in k})"
```

The verified environment has GDAL 3.12.4 with both KML and LIBKML readers. The service checks capabilities on each KML upload; absent readers produce HTTP 503 with an actionable message. All spatial KML layers are read, rather than only the first folder. Attributes are those exposed by GDAL; KML style, presentation and arbitrary XML elements are not losslessly retained.

## Endpoints

| Method | Path | Result |
| --- | --- | --- |
| POST | `/api/files/` | Multipart field `file`; HTTP 201 metadata after successful processing |
| GET | `/api/files/{id}/` | Metadata, or HTTP 404 for an unknown UUID |
| GET | `/api/files/{id}/measurements/` | Measurement envelope, or HTTP 404 |

```bash
curl -X POST http://127.0.0.1:8000/api/files/ -F 'file=@features.kml'
curl -X POST http://127.0.0.1:8000/api/files/ -F 'file=@shapefile.zip'
curl http://127.0.0.1:8000/api/files/ebf802e4-9962-4b2f-a997-a396285d55be/
curl http://127.0.0.1:8000/api/files/ebf802e4-9962-4b2f-a997-a396285d55be/measurements/
```

Replace the example UUID with the ID returned by your upload. Illustrative metadata:

```json
{
  "id": "ebf802e4-9962-4b2f-a997-a396285d55be",
  "filename": "features.kml",
  "feature_count": 1,
  "crs": "EPSG:4326",
  "status": "COMPLETED"
}
```

Illustrative measurement envelope (rounded value):

```json
{
  "file_id": "ebf802e4-9962-4b2f-a997-a396285d55be",
  "source_crs": "EPSG:4326",
  "measurement_crs": "EPSG:32643",
  "features": [
    {
      "feature_id": 0,
      "geometry_type": "LineString",
      "geometry": {"type": "LineString", "coordinates": [[77, 12], [77.001, 12]]},
      "crs": "EPSG:4326",
      "properties": {"Name": "line"},
      "measurement": 109.0,
      "measurement_unit": "m",
      "measurement_status": "MEASURED"
    }
  ]
}
```

Feature IDs are zero-based indices in processed layer order, stable for subsequent requests for that upload. Properties use valid JSON values; missing values become null and dates use ISO strings. Source geometry includes Z coordinates when present, but measurements are horizontal, two-dimensional values. The geometry objects have GeoJSON shape, but a projected source CRS does **not** conform to RFC 7946's WGS84 coordinate requirement. Consumers must use the declared CRS.

## Processing and architecture

```text
app/
  main.py                         app creation and per-app store
  api/routes.py                   HTTP upload/retrieval endpoints
  core/config.py                  resource limits
  core/upload_limit.py            request body limit before multipart parsing
  schemas/files.py                metadata response contract
  schemas/measurements.py         envelope and feature response contracts
  services/file_service.py        validation, UUIDs and atomic record publication
  services/geospatial_service.py  ZIP/KML reading, CRS selection and measurements
tests/
  conftest.py                     generated datasets and isolated TestClient
  test_files.py                   API behavior and consistency
  test_geospatial.py              archive safety, parser limits and cleanup
  test_measurements.py            numerical correctness and feature preservation
pyproject.toml
uv.lock
.gitignore
README.md
```

Uploads pass through a 21 MiB request-body limit (including multipart framing) and a 20 MiB file limit. Geospatial work runs in a worker thread so it does not block the async event loop. The service validates the extension and nonempty content, then processes inside a temporary directory. ZIP members are checked before extraction: traversal, absolute/Windows-style paths, symlinks, encryption and case-insensitive duplicates are rejected. Expanded contents are capped at 100 MiB and 100 entries. Exactly one `.shp` is required, alongside matching `.shx` and `.dbf`; nested directories and case-insensitive sidecar extensions are supported. CRS must be readable from `.prj` metadata. Multiple Shapefiles are rejected to avoid silently choosing one.

KML is first parsed with defusedxml to reject malformed XML, DTDs and entities, including encoded XML. Network links are rejected. GDAL then reads spatial layers through GeoPandas with a 10,000-feature limit. Temporary input files are removed on both success and failure. Metadata and measurements are constructed before a single store insertion; unsuccessful uploads never create successful metadata. Since processing is synchronous from the caller's perspective, only completed records are published; no partially populated PROCESSING/FAILED records are exposed.

## CRS and measurements

Source CRS and geometry are preserved. A copy of the dataset's nonempty geometries is transformed to WGS84 to inspect its extent. GeoPandas estimates a UTM CRS from that extent; PyProj verifies its area-of-use bounds and transforms each measurable geometry with explicit XY axis order.

The whole dataset must fit inside the selected UTM zone's area of use, have longitude span at most 6 degrees and latitude span at most 8 degrees, stay between 80°S and 84°N, and not straddle the equator. Northern and southern UTM zones are selected automatically. Coordinates are never measured in degrees. Existing projected CRS units are not trusted: all measurements use the selected metre-based UTM CRS, including uploads in Web Mercator or feet-based CRSs.

This is intentionally conservative. Cross-zone, dateline-spanning, polar and wider datasets retain their features but receive `UNSUITABLE_EXTENT` and null measurements. UTM is conformal, not equal-area, and measurements have projection distortion; these are suitable local assignment measurements, not cadastral or survey-grade guarantees. A global equal-area fallback was considered but would not solve length distortion or dateline handling. A future implementation could select local equal-area projections for large polygon extents and a separate geodesic or regional strategy for lines, with explicit accuracy requirements.

| Geometry/condition | Measurement | Status |
| --- | --- | --- |
| Polygon, MultiPolygon | Area in `m²` | MEASURED |
| LineString, MultiLineString | Length in `m` | MEASURED |
| Point, MultiPoint | null | NOT_APPLICABLE |
| GeometryCollection or another unsupported type | null | UNSUPPORTED_GEOMETRY |
| Invalid geometry | null; geometry is preserved without repair | INVALID_GEOMETRY |
| Empty geometry | null | EMPTY_GEOMETRY |
| Missing geometry | null geometry and measurement | MISSING_GEOMETRY |
| Unsuitable extent | null | UNSUITABLE_EXTENT |
| Failed projection of a feature | null | TRANSFORMATION_FAILED |

Invalid geometries are not silently repaired: repair can change topology and therefore area. Non-finite source coordinates cause rejection because they cannot be represented in valid JSON. Unknown or unusable source CRS causes HTTP 422; Shapefiles never silently default to EPSG:4326. KML's geographic CRS comes from the format's reader.

## Errors and verification

Errors use FastAPI's `{"detail": "..."}` contract, without exception traces or filesystem paths. Empty files produce 400, unsupported extensions 415, oversized inputs 413, malformed/unreadable/unsafe datasets 422, and unavailable KML drivers 503. Missing multipart fields and malformed UUID parameters produce standard validation 422 responses.

Tests generate KML strings and temporary zipped Shapefiles. They exercise upload/retrieval, UUID uniqueness, numerical comparisons against independent PyProj geodesic calculations with tolerances, preservation of coordinates/properties, northern/southern projections, invalid/empty/unsupported geometry, large/cross-zone extents, missing CRS, upload/feature/archive limits, driver unavailability, unsafe archives and temporary cleanup. No tests depend on a downloaded fixture. Executed verification: 41 tests passed, Ruff lint and formatting checks passed, and a live Uvicorn smoke test verified OpenAPI, KML upload (201) and measurements retrieval (200).

## Decisions and limitations

An in-memory dictionary keeps the assignment understandable and makes metadata/measurements consistent within one app process. It does not survive restarts, support multiple workers, or provide horizontal scalability. Repeated uploads consume memory until restart; there is no aggregate upload quota, eviction or deletion endpoint. A database would be appropriate for persistence but would add unnecessary infrastructure here.

GeoPandas with explicit Pyogrio provides one reader path for both formats. Direct custom KML parsing was avoided for geometry extraction because KML includes layered and mixed geometry structures that GDAL already handles. Request/ZIP/feature limits bound common input sizes but do not provide a parser CPU timeout, coordinate-count limit or process sandbox. Public deployment should add worker-process isolation, timeouts and quotas. Upload names are only metadata and never used as local paths. Client cancellation may allow an already-running worker thread to finish processing.

No frontend, authentication, deployment infrastructure or database is included. Tests verified this local Linux binary-wheel environment; other GDAL builds and operating systems are not verified. The dependency set currently emits an upstream Starlette TestClient deprecation warning for httpx; it does not fail tests. The missing-CRS fixture deliberately emits a Pyogrio warning when writing a Shapefile without projection metadata.

## What I learned — draft to review before submission

I can explain why measuring longitude/latitude coordinates directly gives degrees rather than metres, and how preserving the original geometry while projecting a copy avoids confusing API consumers. The tests compare projected measurements with geodesic values, which gives me a concrete way to check the units and transformation.

I also verified that KML support depends on GDAL's installed drivers and that a document may have multiple spatial layers. The archive tests demonstrate why validating filenames before extraction matters. An edge case with geometry-only datasets showed that an empty attribute table still needs one properties object for each feature.

These statements describe verified implementation findings. I should edit them to reflect my own understanding and only submit claims I can explain.

## Future scope

Persistent metadata/features, asynchronous jobs with explicit processing states, pagination, authentication, per-user quotas and cleanup, isolated parsers with timeouts, and deployment monitoring are useful next steps. Broader geographic measurement strategies should be added only alongside accuracy requirements and tests for polar and dateline geometries.
