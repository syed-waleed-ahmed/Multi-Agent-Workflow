"""Tenant scope: every stored or retrieved record belongs to exactly one scope."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

ScopeId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[\w.:-]+$"),
]


class Scope(BaseModel):
    """Identifies the organisation and user that own a run, artifact or memory record.

    Repositories require a scope on every call, so there is no code path that
    can read across tenants by accident.
    """

    model_config = ConfigDict(frozen=True)

    org_id: ScopeId
    user_id: ScopeId

    @classmethod
    def local(cls) -> Scope:
        """The single-user scope used by the CLI and local library use."""
        return cls(org_id="local", user_id="local")
