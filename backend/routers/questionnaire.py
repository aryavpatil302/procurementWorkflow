from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ProcurementRequestORM
from backend.services.questionnaire import get_questions

router = APIRouter()


@router.get("/requests/{request_id}/questionnaire")
def get_questionnaire(request_id: str, db: Session = Depends(get_db)):
    req = db.get(ProcurementRequestORM, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    if not req.questionnaire_depth:
        raise HTTPException(
            status_code=400,
            detail="This request has no questionnaire depth set. "
                   "The intake conversation may not have completed.",
        )

    questions = get_questions(req.questionnaire_depth)

    return {
        "request_id": req.id,
        "supplier_name": req.supplier_name,
        "questionnaire_depth": req.questionnaire_depth,
        "total_questions": len(questions),
        "questions": questions,
    }
