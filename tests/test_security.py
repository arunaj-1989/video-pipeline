import pytest

from universal_video_maker.errors import ForbiddenError
from universal_video_maker.security import (
    Permission,
    Principal,
    Role,
    has_permission,
    require_permission,
)


def test_admin_has_every_declared_permission() -> None:
    admin = Principal(subject="admin-1", roles=frozenset({Role.ADMIN}))

    for permission in Permission:
        assert has_permission(admin, permission)


def test_fact_reviewer_can_review_claims_but_cannot_release() -> None:
    reviewer = Principal(subject="reviewer-1", roles=frozenset({Role.FACT_REVIEWER}))

    assert has_permission(reviewer, Permission.CLAIMS_REVIEW)
    assert not has_permission(reviewer, Permission.PUBLISH_RELEASE)


def test_require_permission_raises_explicit_forbidden_error() -> None:
    viewer = Principal(subject="viewer-1", roles=frozenset({Role.VIEWER}))

    with pytest.raises(ForbiddenError):
        require_permission(viewer, Permission.USERS_MANAGE)


def test_multiple_roles_union_their_permissions() -> None:
    principal = Principal(
        subject="editor-reviewer",
        roles=frozenset({Role.EDITOR, Role.FACT_REVIEWER}),
    )

    assert has_permission(principal, Permission.CLAIMS_REVIEW)
    assert has_permission(principal, Permission.EPISODES_WRITE)

