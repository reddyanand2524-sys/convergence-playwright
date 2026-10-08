import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.email import send_password_reset_email
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.models.password_reset import PasswordResetToken
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
)


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    existing_user = db.scalar(
        select(User).where(
            or_(
                User.email == request.email,
                User.mobile_number == request.mobile_number,
            )
        )
    )

    if existing_user:
        if existing_user.email == request.email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mobile number already registered",
        )

    hashed_password = hash_password(request.password)

    user = User(
        full_name=request.full_name,
        email=str(request.email).lower(),
        mobile_number=request.mobile_number,
        password_hash=hashed_password,
        is_active=True,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "Account created successfully",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "mobile_number": user.mobile_number,
            "is_active": user.is_active,
        },
    }


@router.post("/login")
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User).where(
            User.email == str(request.email).lower()
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(
        request.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    access_token = create_access_token(
        user_id=user.id
    )

    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "mobile_number": user.mobile_number,
            "is_active": user.is_active,
        },
    }


@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user),
):
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "mobile_number": current_user.mobile_number,
        "is_active": current_user.is_active,
    }


@router.post("/forgot-password")
def forgot_password(
    email: str,
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User).where(
            User.email == email.lower()
        )
    )

    if not user:
        return {
            "message": (
                "If an account exists for this email, "
                "a password reset link has been sent."
            )
        }

    reset_token = secrets.token_urlsafe(32)

    expires_at = datetime.utcnow() + timedelta(minutes=30)

    password_reset = PasswordResetToken(
        user_id=user.id,
        token=reset_token,
        expires_at=expires_at,
        used=False,
    )

    db.add(password_reset)
    db.commit()

    # Send password reset email
    send_password_reset_email(
        recipient_email=user.email,
        reset_token=reset_token,
    )

    return {
        "message": (
            "If an account exists for this email, "
            "a password reset link has been sent."
        )
    }


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    reset_record = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token == request.token
        )
    )

    if not reset_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if reset_record.used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used",
        )

    if reset_record.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user = db.scalar(
        select(User).where(
            User.id == reset_record.user_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset request",
        )

    user.password_hash = hash_password(
        request.new_password
    )

    reset_record.used = True

    db.commit()

    return {
        "message": "Password reset successfully"
    }