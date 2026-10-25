from typing import Literal

from pydantic import BaseModel


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    # One entry per dependency checked, e.g. {"blastproof_db": "ok", "company_db": "ok"}.
    checks: dict[str, Literal["ok"]]
