"""Composition root for the membership context."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.utils import mask_phone, utcnow
from app.membership.application.administration import (
    BlacklistUser,
    ChangeRole,
    DeleteUser,
    ForceLogout,
    UnblacklistUser,
)
from app.membership.application.review import ApplyForMembership, ReviewApplication
from app.membership.infrastructure.repositories import (
    SmsApprovalNotifier,
    SqlAuditLog,
    SqlLoginSessionDeactivator,
    SqlUserStore,
)


@dataclass(frozen=True)
class MembershipContext:
    apply: ApplyForMembership
    review: ReviewApplication
    change_role: ChangeRole
    blacklist: BlacklistUser
    unblacklist: UnblacklistUser
    force_logout: ForceLogout
    delete_user: DeleteUser


def membership(db: Session) -> MembershipContext:
    store = SqlUserStore(db)
    audits = SqlAuditLog(db)
    sessions = SqlLoginSessionDeactivator(db)
    notifier = SmsApprovalNotifier()
    now = utcnow
    return MembershipContext(
        apply=ApplyForMembership(store, now),
        review=ReviewApplication(store, audits, notifier, mask_phone),
        change_role=ChangeRole(store, audits),
        blacklist=BlacklistUser(store, audits, sessions, now),
        unblacklist=UnblacklistUser(store, audits),
        force_logout=ForceLogout(store, audits, sessions, now),
        delete_user=DeleteUser(store, audits, sessions, now, mask_phone),
    )
