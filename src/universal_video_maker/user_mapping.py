"""Map an authenticated Keycloak principal to its internal users row."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import User
from universal_video_maker.errors import ConflictError, ForbiddenError
from universal_video_maker.security import Principal, Role

ROLE_PRIORITY = (Role.ADMIN, Role.FACT_REVIEWER, Role.EDITOR, Role.PUBLISHER, Role.VIEWER)


def ensure_internal_user(session: Session, principal: Principal) -> UUID:
    """Return the users.id for the principal, creating the row on first use.

    Keycloak subjects are UUIDs, so the subject is used directly as the primary key.
    """
    try:
        user_id = UUID(principal.subject)
    except ValueError as error:
        raise ForbiddenError("Authenticated subject is not a valid user identifier.") from error

    role = next((item for item in ROLE_PRIORITY if item in principal.roles), None)
    if role is None:
        raise ForbiddenError("Authenticated user has no application role.")

    user = session.get(User, user_id)
    if user is None:
        email = (principal.email or f"{user_id}@keycloak.invalid").casefold()
        if session.scalar(select(User.id).where(User.email == email)) is not None:
            raise ConflictError("Another user already uses this email address.")
        user = User(id=user_id, email=email, role=role.value)
        session.add(user)
    elif user.role not in {item.value for item in principal.roles}:
        user.role = role.value
    session.flush()
    return user_id
