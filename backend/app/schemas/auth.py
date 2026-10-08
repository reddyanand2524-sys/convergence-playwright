from pydantic import BaseModel, EmailStr, Field, model_validator


class RegisterRequest(BaseModel):
    full_name: str = Field(
        ...,
        min_length=2,
        max_length=150,
    )

    email: EmailStr

    mobile_number: str = Field(
        ...,
        min_length=10,
        max_length=20,
    )

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
    )

    confirm_password: str = Field(
        ...,
        min_length=8,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_registration(self):
        self.full_name = self.full_name.strip()
        self.mobile_number = self.mobile_number.strip()

        if not self.full_name:
            raise ValueError("Full name is required")

        if not self.mobile_number.isdigit():
            raise ValueError(
                "Mobile number must contain only digits"
            )

        if len(self.mobile_number) < 10 or len(self.mobile_number) > 15:
            raise ValueError(
                "Mobile number must contain 10 to 15 digits"
            )

        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")

        return self


class LoginRequest(BaseModel):
    email: EmailStr

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
    )


class ResetPasswordRequest(BaseModel):
    token: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    new_password: str = Field(
        ...,
        min_length=8,
        max_length=128,
    )

    confirm_password: str = Field(
        ...,
        min_length=8,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")

        return self