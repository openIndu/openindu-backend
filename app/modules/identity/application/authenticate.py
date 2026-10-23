"""SMS authentication use case; persistence and verification enter through ports."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.modules.identity.domain.account import AccountState, initial_role, require_available, require_unregistered
from app.shared.domain.auth import AuthError, BizCode


class Account(AccountState, Protocol):
    id: int
    phone: str
    role: str


class AccountRepository(Protocol):
    def find_by_phone(self, phone: str) -> Account | None: ...

    def count(self) -> int: ...

    def create(self, phone: str, role: str, now: datetime) -> Account: ...

    def record_login(self, account: Account, now: datetime) -> None: ...


class CodeVerifier(Protocol):
    def __call__(self, phone: str, code: str) -> bool: ...


class AuthenticationIntent(StrEnum):
    LOGIN = "login"
    REGISTER = "register"
    SIGN_IN = "sign_in"


@dataclass(frozen=True)
class AuthenticationResult:
    account: Account
    outcome: BizCode
    is_new_user: bool


class AuthenticateBySms:
    def __init__(self, accounts: AccountRepository, verify_code: CodeVerifier, now: datetime) -> None:
        self.accounts = accounts
        self.verify_code = verify_code
        self.now = now

    def execute(self, phone: str, code: str, intent: AuthenticationIntent) -> AuthenticationResult:
        if not self.verify_code(phone, code):
            raise AuthError(BizCode.AUTH_INVALID_CODE)

        account = self.accounts.find_by_phone(phone)
        if intent is AuthenticationIntent.REGISTER:
            require_unregistered(account)
            account = self.accounts.create(phone, initial_role(self.accounts.count()), self.now)
            return AuthenticationResult(account, BizCode.AUTH_REGISTER_SUCCESS, True)

        if account is None:
            if intent is AuthenticationIntent.LOGIN:
                raise AuthError(BizCode.AUTH_USER_NOT_FOUND)
            account = self.accounts.create(phone, initial_role(self.accounts.count()), self.now)
            return AuthenticationResult(account, BizCode.AUTH_ACCOUNT_CREATED, True)

        require_available(account)
        self.accounts.record_login(account, self.now)
        return AuthenticationResult(account, BizCode.AUTH_LOGIN_SUCCESS, False)
