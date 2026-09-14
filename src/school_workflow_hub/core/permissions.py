"""Executable, default-deny permission enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class PermissionDenied(PermissionError):
    pass


class PermissionScope(str, Enum):
    LOCAL_READ = "local.read"
    LOCAL_CREATE = "local.create"
    EXTERNAL_READ = "external.read"
    EXTERNAL_CREATE_UPDATE = "external.update"
    DESTRUCTIVE = "destructive"


def ensure_dry_run(mode: str) -> None:
    """Centrally reject write mode in the shipped synthetic-only foundation.

    A later live pilot must replace this hard boundary through a separately
    reviewed implementation, not by changing a config value or consuming an
    approval record.
    """

    if mode != "dry_run":
        raise PermissionDenied("only dry_run mode is available in the synthetic foundation")


EXTERNAL_SCOPES = {
    PermissionScope.EXTERNAL_READ.value,
    PermissionScope.EXTERNAL_CREATE_UPDATE.value,
    PermissionScope.DESTRUCTIVE.value,
}


@dataclass(frozen=True)
class PermissionPolicy:
    """Current-session policy.

    `allow_external_writes` is deliberately fixed false for this foundation.
    Later production work must introduce a separately reviewed policy change, not
    merely flip a setting from a browser or environment variable.
    """

    allowed: frozenset[str]
    default: str = "deny"
    allow_external_writes: bool = False

    @classmethod
    def from_allowed(cls, allowed: Iterable[str]) -> "PermissionPolicy":
        return cls(frozenset(allowed))

    def allows(self, scope: str) -> bool:
        if scope in (PermissionScope.EXTERNAL_CREATE_UPDATE.value, PermissionScope.DESTRUCTIVE.value):
            return False
        return scope in self.allowed

    def require(self, scope: str, target: str) -> None:
        if not self.allows(scope):
            raise PermissionDenied(f"denied {scope} for {target}")


def require_external_execution(policy: PermissionPolicy, target: str) -> None:
    """A hard stop for every live create/update path in the current build."""
    policy.require(PermissionScope.EXTERNAL_CREATE_UPDATE.value, target)
