from fastapi import FastAPI

from app.api.routes import router
from app.core.upload_limit import UploadLimitMiddleware
from app.services.file_service import FileStore


def create_app() -> FastAPI:
    application = FastAPI(title="AEREO Geospatial Measurement API", version="0.1.0")
    application.add_middleware(UploadLimitMiddleware)
    application.state.file_store = FileStore()
    application.include_router(router)
    return application


app = create_app()
