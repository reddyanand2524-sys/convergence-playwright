from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.schedule import VALID_FREQUENCIES, FREQUENCY_INTERVAL


class CreateScheduleRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    browser: str = Field(..., min_length=1, max_length=30)
    frequency: str = Field(...)
    interval_value: int | None = Field(default=None)
    start_at: datetime = Field(...)
    enabled: bool = Field(default=True)

    @model_validator(mode="after")
    def _validate(self):
        if self.frequency not in VALID_FREQUENCIES:
            raise ValueError(
                f"frequency must be one of {VALID_FREQUENCIES}"
            )

        if self.frequency == FREQUENCY_INTERVAL:
            if self.interval_value is None:
                raise ValueError(
                    "interval_value is required when frequency is INTERVAL"
                )
            if self.interval_value <= 0:
                raise ValueError("interval_value must be > 0")
        else:
            if self.interval_value is not None:
                raise ValueError(
                    "interval_value must not be supplied unless frequency is INTERVAL"
                )

        return self


class UpdateScheduleRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    browser: str | None = Field(default=None, min_length=1, max_length=30)
    frequency: str | None = Field(default=None)
    interval_value: int | None = Field(default=None)
    start_at: datetime | None = Field(default=None)
    enabled: bool | None = Field(default=None)

    @model_validator(mode="after")
    def _validate(self):
        if self.frequency is not None:
            if self.frequency not in VALID_FREQUENCIES:
                raise ValueError(
                    f"frequency must be one of {VALID_FREQUENCIES}"
                )

            if self.frequency == FREQUENCY_INTERVAL:
                if self.interval_value is not None and self.interval_value <= 0:
                    raise ValueError("interval_value must be > 0")
            else:
                if self.interval_value is not None:
                    raise ValueError(
                        "interval_value must not be supplied unless frequency is INTERVAL"
                    )
        else:
            if self.interval_value is not None and self.interval_value <= 0:
                raise ValueError("interval_value must be > 0")

        return self


class ScheduleResponse(BaseModel):
    id: int
    project_id: int
    name: str
    browser: str
    frequency: str
    interval_value: int | None
    start_at: datetime
    next_run_at: datetime
    last_run_at: datetime | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
