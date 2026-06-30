"""
app/utils/exceptions.py
────────────────────────
Centralised HTTP exception helpers.
Using explicit factory functions (instead of bare HTTPException) gives us:
  - A single place to adjust error formats project-wide
  - Cleaner call sites: raise NotFoundError("Leave request") vs raise HTTPException(...)
"""
from fastapi import HTTPException, status


def NotFoundError(entity: str, identifier: str | None = None) -> HTTPException:
    detail = f"{entity} not found"
    if identifier:
        detail = f"{entity} '{identifier}' not found"
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def ForbiddenError(detail: str = "You do not have permission to perform this action") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def UnauthorizedError(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def BadRequestError(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def ConflictError(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def UnprocessableError(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )
