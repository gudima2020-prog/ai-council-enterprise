from time import perf_counter
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core.logging import (
    LoggerManager,
    clear_log_context,
    new_correlation_id,
    new_request_id,
    set_log_context,
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        logger = LoggerManager.get_logger("api")
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or new_correlation_id()
        )
        set_log_context(
            request_id=request_id,
            correlation_id=correlation_id,
        )

        started = perf_counter()
        logger.info(
            "HTTP request started method=%s path=%s",
            request.method,
            request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "HTTP request failed method=%s path=%s",
                request.method,
                request.url.path,
            )
            clear_log_context()
            raise

        duration_ms = round((perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id

        logger.info(
            "HTTP request completed method=%s path=%s status=%s duration_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        clear_log_context()
        return response
