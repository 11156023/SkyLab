"""協作實驗室 Pair Mode API（E5）。"""

import logging
import uuid

from fastapi import APIRouter
from sqlmodel import Session

from app.api.deps import CurrentUser, SessionDep
from app.models import User
from app.schemas import PairSessionCreate, PairSessionPublic
from app.schemas.common import Message
from app.services.classroom import pair_service
from app.services.classroom.pair_service import PairSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pair-sessions", tags=["pair-sessions"])


def _display_name(session: Session, user_id: uuid.UUID) -> str | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    return user.full_name or user.email


def _to_public(session: Session, pair: PairSession) -> PairSessionPublic:
    return PairSessionPublic(
        id=pair.id,
        vmid=pair.vmid,
        owner_id=pair.owner_id,
        invitee_id=pair.invitee_id,
        owner_name=_display_name(session, pair.owner_id),
        invitee_name=_display_name(session, pair.invitee_id),
        created_at=pair.created_at,
    )


@router.post("", response_model=PairSessionPublic, status_code=201)
async def create_pair_session(
    body: PairSessionCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> PairSessionPublic:
    pair = await pair_service.create_pair(
        session, current_user, vmid=body.vmid, invitee_user_id=body.invitee_user_id
    )
    return _to_public(session, pair)


@router.get("/mine", response_model=list[PairSessionPublic])
def list_my_pair_sessions(
    session: SessionDep, current_user: CurrentUser
) -> list[PairSessionPublic]:
    return [_to_public(session, p) for p in pair_service.list_mine(current_user)]


@router.delete("/{session_id}", response_model=Message)
async def end_pair_session(
    session_id: str, current_user: CurrentUser
) -> Message:
    await pair_service.end_pair(current_user, session_id)
    return Message(message="Pair session ended")
