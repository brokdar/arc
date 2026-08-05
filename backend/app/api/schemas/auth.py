"""Request/response schemas for the auth endpoints."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Credentials for the single-user login."""

    password: str


class SessionStatus(BaseModel):
    """Whether the caller currently holds an authenticated session."""

    authenticated: bool
