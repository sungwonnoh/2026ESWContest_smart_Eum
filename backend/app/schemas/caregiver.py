from pydantic import BaseModel, Field


class CaregiverSummaryRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=31)


class CaregiverSummaryResponse(BaseModel):
    days: int
    text: str
    sent: bool
    reason: str = ""
