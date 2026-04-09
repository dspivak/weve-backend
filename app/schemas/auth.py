import re
from pydantic import BaseModel, EmailStr, Field, field_validator


def _is_strong_password(password: str) -> bool:
    return (
        len(password) >= 8
        and re.search(r"[A-Z]", password) is not None
        and re.search(r"\d", password) is not None
        and re.search(r"[^A-Za-z0-9]", password) is not None
    )


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1)
    username: str = Field(..., min_length=2, pattern=r"^[a-zA-Z0-9_]+$")
    zip_code: str | None = None
    phone: str | None = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not _is_strong_password(v):
            raise ValueError(
                "Password must be at least 8 characters and contain an uppercase letter, a number, and a special character."
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    username: str
    bio: str | None = None
    avatar_url: str | None = None
    contributions_count: int = 0
    collaborations_count: int = 0

class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(None, min_length=1)
    bio: str | None = None
    avatar_url: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: UserResponse


class SignupSuccessResponse(BaseModel):
    message: str = "Please check your email and verify your account."


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ResendVerificationResponse(BaseModel):
    message: str = "Verification email sent. Please check your inbox."
