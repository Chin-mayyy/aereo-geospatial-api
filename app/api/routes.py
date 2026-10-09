from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import MAX_UPLOAD_BYTES
from app.schemas.files import FileMetadata
from app.schemas.measurements import Measurements
from app.services.geospatial_service import DatasetError

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/", response_model=FileMetadata, status_code=201)
async def upload_file(request: Request, file: Annotated[UploadFile, File()]):
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload exceeds the 20 MiB limit.")
        return await run_in_threadpool(
            request.app.state.file_store.process, file.filename or "", content
        )
    except DatasetError as error:
        raise HTTPException(error.status_code, str(error)) from error
    finally:
        await file.close()


def get_record(request: Request, identifier: UUID):
    record = request.app.state.file_store.records.get(identifier)
    if record is None:
        raise HTTPException(404, "File not found.")
    return record


@router.get("/{identifier}/", response_model=FileMetadata)
def get_file(request: Request, identifier: UUID):
    return get_record(request, identifier)[0]


@router.get("/{identifier}/measurements/", response_model=Measurements)
def get_measurements(request: Request, identifier: UUID):
    return get_record(request, identifier)[1]
