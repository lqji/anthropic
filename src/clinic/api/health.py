from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clinic.api.deps import get_deps
from clinic.db import ping
from clinic.deps import Deps

router = APIRouter()


@router.get("/health")
def health(deps: Annotated[Deps, Depends(get_deps)]) -> JSONResponse:
    if ping(deps.pool):
        return JSONResponse(
            {"status": "ok", "database": "ok", "version": deps.settings.app_version}
        )
    return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
