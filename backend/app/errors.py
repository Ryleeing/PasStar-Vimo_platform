"""Unified error format: {"error": {"code", "message", "details"}}. Any module raises ApiError; main.py turns it into the response.
The codes are the ones in schemas.ErrorDetail."""
from __future__ import annotations

from fastapi.responses import JSONResponse

HTTP_STATUS = {
    "VALIDATION_ERROR": 422, "PROTOCOL_INVALID": 403, "OUT_OF_SCOPE": 403, "RATE_LIMITED": 429, "PROBE_LIMIT": 429,
    "QUOTE_EXPIRED": 409, "QUOTE_CHANGED": 409, "RETRY_LIMIT": 409, "NOT_FOUND": 404, "UPSTREAM_ERROR": 502,
    "SELF_APPROVAL_FORBIDDEN": 403, "NOT_APPROVER": 403, "AUTHORIZATION_INVALID": 403,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def response(code: str, message: str, details: dict) -> JSONResponse:
    return JSONResponse(status_code=HTTP_STATUS[code],
                        content={"error": {"code": code, "message": message, "details": details}})
