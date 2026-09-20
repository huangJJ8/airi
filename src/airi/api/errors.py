import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from airi.core.exceptions import AIRIError
from airi.core.schemas import StrictSchema

logger = logging.getLogger("airi.errors")


class ErrorDetail(StrictSchema):
    code: str
    message: str
    request_id: str


class ErrorResponse(StrictSchema):
    error: ErrorDetail


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status, content=body.model_dump(), headers={"X-Request-ID": request_id}
    )


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_error",
        extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
    )
    return error_response(request, 500, "internal_error", "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AIRIError)
    async def domain_error(request: Request, exc: AIRIError) -> JSONResponse:
        message = exc.public_message or (
            exc.message if exc.status_code < 500 else "Internal server error"
        )
        return error_response(request, exc.status_code, exc.code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "validation_error", "Request schema validation failed")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(request, exc.status_code, "http_error", "HTTP request failed")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    app.add_exception_handler(Exception, unexpected_error)
