import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import bind_ids


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def setup_logging() -> None:
    """Имя из каркаса Хекслета. То же, что configure_logging."""
    configure_logging()


def get_logger(name: str | None = None):
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()


def bind_log(**kwargs: object) -> None:
    payload = {key: value for key, value in kwargs.items() if value is not None}
    structlog.contextvars.bind_contextvars(**payload)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        bind_ids(request_id=request_id)
        bind_log(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        get_logger().info(
            "request",
            request_id=request_id,
            operation=f"{request.method} {request.url.path}",
            status=response.status_code,
        )
        response.headers["x-request-id"] = request_id
        return response
