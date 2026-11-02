from pydantic import BaseModel


class ErrorBody(BaseModel):
    """Shape of every error response."""

    code: str
    detail: str | None = None
    request_id: str | None = None
