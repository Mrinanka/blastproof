from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.actions import get_policy
from app.api.deps import PageParams
from app.core.auth import CurrentUser, get_current_user
from app.core.errors import ErrorResponse
from app.schemas.policies import RulePage
from app.services.policy import Policy
from app.services.policy_view import rule_views

router = APIRouter(prefix="/v1/policies", tags=["policies"])


@router.get(
    "",
    response_model=RulePage,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_policies(
    page: Annotated[PageParams, Query()],
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    policy: Annotated[Policy, Depends(get_policy)],
) -> RulePage:
    """The rules every write is checked against, in the order they are written in rules.yaml."""
    views = rule_views(policy)
    return RulePage(
        items=views[page.offset : page.offset + page.limit],
        total=len(views),
        limit=page.limit,
        offset=page.offset,
    )
