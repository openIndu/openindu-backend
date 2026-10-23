"""Identity rules and orchestration without a database or HTTP server."""

from dataclasses import dataclass
from datetime import datetime

import pytest

from app.domain.auth import AuthError, BizCode
from app.modules.identity.application.authenticate import AuthenticateBySms, AuthenticationIntent


@dataclass
class Account:
    id: int = 1
    phone: str = "13800138000"
    role: str = "user"
    deleted_at: datetime | None = None
    is_active: bool = True
    is_blacklisted: bool = False
    last_login: datetime | None = None


class Accounts:
    def __init__(self, account: Account | None = None, count: int = 1) -> None:
        self.account = account
        self.total = count
        self.created = 0
        self.logins = 0

    def find_by_phone(self, phone: str) -> Account | None:
        return self.account if self.account and self.account.phone == phone else None

    def count(self) -> int:
        return self.total

    def create(self, phone: str, role: str, now: datetime) -> Account:
        self.created += 1
        self.account = Account(phone=phone, role=role, last_login=now)
        return self.account

    def record_login(self, account: Account, now: datetime) -> None:
        self.logins += 1
        account.last_login = now


NOW = datetime(2026, 10, 22)


def authenticate(accounts: Accounts, intent: AuthenticationIntent, *, valid: bool = True):
    return AuthenticateBySms(accounts, lambda _phone, _code: valid, NOW).execute(
        "13800138000", "123456", intent
    )


def test_first_registration_assigns_admin_and_returns_registration_outcome():
    accounts = Accounts(count=0)
    result = authenticate(accounts, AuthenticationIntent.REGISTER)
    assert (result.account.role, result.outcome, result.is_new_user) == (
        "admin", BizCode.AUTH_REGISTER_SUCCESS, True
    )
    assert accounts.created == 1


def test_sign_in_creates_regular_account_when_others_exist():
    accounts = Accounts(count=2)
    result = authenticate(accounts, AuthenticationIntent.SIGN_IN)
    assert (result.account.role, result.outcome, result.is_new_user) == (
        "user", BizCode.AUTH_ACCOUNT_CREATED, True
    )


def test_existing_account_records_login_and_returns_explicit_outcome():
    accounts = Accounts(Account())
    result = authenticate(accounts, AuthenticationIntent.SIGN_IN)
    assert (result.outcome, result.is_new_user, accounts.logins) == (BizCode.AUTH_LOGIN_SUCCESS, False, 1)
    assert result.account.last_login == NOW


@pytest.mark.parametrize(
    ("intent", "account", "code"),
    [
        (AuthenticationIntent.LOGIN, None, BizCode.AUTH_USER_NOT_FOUND),
        (AuthenticationIntent.REGISTER, Account(), BizCode.AUTH_USER_EXISTS),
        (AuthenticationIntent.REGISTER, Account(deleted_at=NOW), BizCode.AUTH_PHONE_PREVIOUSLY_DELETED),
        (AuthenticationIntent.LOGIN, Account(deleted_at=NOW), BizCode.AUTH_ACCOUNT_REMOVED),
        (AuthenticationIntent.SIGN_IN, Account(is_active=False), BizCode.AUTH_ACCOUNT_DISABLED),
        (AuthenticationIntent.SIGN_IN, Account(is_blacklisted=True), BizCode.AUTH_ACCOUNT_DISABLED),
    ],
)
def test_account_rules_reject_invalid_transitions(intent, account, code):
    accounts = Accounts(account)
    with pytest.raises(AuthError) as error:
        authenticate(accounts, intent)
    assert error.value.code == code
    assert accounts.created == 0
    assert accounts.logins == 0


def test_invalid_code_stops_before_repository_access():
    class UnreachableAccounts(Accounts):
        def find_by_phone(self, phone: str):
            raise AssertionError("repository should not be queried")

    with pytest.raises(AuthError) as error:
        authenticate(UnreachableAccounts(), AuthenticationIntent.SIGN_IN, valid=False)
    assert error.value.code == BizCode.AUTH_INVALID_CODE
