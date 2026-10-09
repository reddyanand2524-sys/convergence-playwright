from datetime import datetime, timedelta, timezone

import jwt
from pydantic_settings import BaseSettings
from pwdlib import PasswordHash


class Settings(BaseSettings):
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    test_execution_timeout_seconds: int = 120
    # Typed step values (send_keys/type) are NOT persisted or returned by default:
    # they are stored as a "***" marker, because a field's name does not tell
    # whether what was typed is secret. Set PERSIST_TYPED_VALUES=true to keep them
    # (secret-named fields and known secrets are still masked).
    persist_typed_values: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    return password_hash.verify(
        plain_password,
        hashed_password,
    )


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )

    payload = {
        "sub": str(user_id),
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> int:
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    user_id = payload.get("sub")

    if user_id is None:
        raise ValueError("Invalid token")

    return int(user_id)