from dataclasses import dataclass
from enum import StrEnum

from universal_video_maker.errors import ForbiddenError


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    FACT_REVIEWER = "fact_reviewer"
    PUBLISHER = "publisher"
    VIEWER = "viewer"


class Permission(StrEnum):
    TOPICS_READ = "topics:read"
    TOPICS_WRITE = "topics:write"
    CLAIMS_REVIEW = "claims:review"
    EPISODES_READ = "episodes:read"
    EPISODES_WRITE = "episodes:write"
    EPISODES_APPROVE = "episodes:approve"
    RENDER_SUBMIT = "render:submit"
    PUBLISH_RELEASE = "publish:release"
    AUDIT_READ = "audit:read"
    ANALYTICS_READ = "analytics:read"
    ANALYTICS_WRITE = "analytics:write"
    USERS_MANAGE = "users:manage"


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[Role]


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.EDITOR: frozenset(
        {
            Permission.TOPICS_READ,
            Permission.TOPICS_WRITE,
            Permission.EPISODES_READ,
            Permission.EPISODES_WRITE,
            Permission.EPISODES_APPROVE,
            Permission.RENDER_SUBMIT,
        }
    ),
    Role.FACT_REVIEWER: frozenset(
        {
            Permission.TOPICS_READ,
            Permission.CLAIMS_REVIEW,
            Permission.EPISODES_READ,
        }
    ),
    Role.PUBLISHER: frozenset(
        {
            Permission.EPISODES_READ,
            Permission.PUBLISH_RELEASE,
            Permission.ANALYTICS_READ,
            Permission.ANALYTICS_WRITE,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.TOPICS_READ,
            Permission.EPISODES_READ,
            Permission.ANALYTICS_READ,
        }
    ),
}


def has_permission(principal: Principal, permission: Permission) -> bool:
    return any(permission in ROLE_PERMISSIONS[role] for role in principal.roles)


def require_permission(principal: Principal, permission: Permission) -> None:
    if not has_permission(principal, permission):
        raise ForbiddenError()
