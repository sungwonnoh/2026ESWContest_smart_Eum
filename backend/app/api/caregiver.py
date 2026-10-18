from fastapi import APIRouter, Depends

from ..dependencies import Container, get_container
from ..schemas.caregiver import CaregiverSummaryRequest, CaregiverSummaryResponse

router = APIRouter(prefix="/caregiver", tags=["caregiver"])


@router.post("/summary", response_model=CaregiverSummaryResponse)
def send_summary(
    body: CaregiverSummaryRequest,
    c: Container = Depends(get_container),
) -> CaregiverSummaryResponse:
    text = c.caregiver_summary.build(body.days)
    result = c.caregiver_summary.send(body.days)
    return CaregiverSummaryResponse(
        days=body.days, text=text, sent=result.sent, reason=result.reason
    )
