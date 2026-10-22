"""SQLAlchemy implementation of the account repository port."""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User


class SqlAccountRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_by_phone(self, phone: str) -> User | None:
        return self.db.query(User).filter(User.phone == phone).first()

    def count(self) -> int:
        return self.db.query(func.count(User.id)).scalar()

    def create(self, phone: str, role: str, now: datetime) -> User:
        account = User(phone=phone, role=role, is_active=True, is_blacklisted=False, last_login=now)
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        return account

    def record_login(self, account: User, now: datetime) -> None:
        account.last_login = now
        self.db.commit()
        self.db.refresh(account)
