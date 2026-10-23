"""Composition root: assemble identity use cases from a request-scoped session.

This is the only place where settings and SQLAlchemy meet the application
layer; ``api/auth.py`` and tests consume ready-to-run use cases from here.
"""
import random
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.identity.application.authenticate import AuthenticateBySms
from app.modules.identity.application.captcha import (
    ConsumeSliderTicket,
    IssueSliderChallenge,
    VerifySliderChallenge,
)
from app.modules.identity.application.sessions import (
    ChangePhone,
    DeleteAccount,
    EndSession,
    IssueTokenPair,
    RefreshSession,
)
from app.modules.identity.application.verification_codes import SendVerificationCode, VerifySmsCode
from app.modules.identity.infrastructure.accounts import SqlAccountRepository
from app.modules.identity.infrastructure.slider_challenges import PilChallengeRenderer, SqlSliderChallengeRepository
from app.modules.identity.infrastructure.tokens import JwtTokenCodec, SqlLoginSessionStore, SqlTokenBlacklist
from app.modules.identity.infrastructure.verification_codes import ProviderSmsGateway, SqlCodeStore
from app.shared.core.config import settings
from app.shared.core.utils import utcnow


@dataclass(frozen=True)
class IdentityContext:
    issue_captcha: IssueSliderChallenge
    verify_captcha: VerifySliderChallenge
    consume_ticket: ConsumeSliderTicket
    send_code: SendVerificationCode
    verify_code: VerifySmsCode
    authenticate: AuthenticateBySms
    issue_tokens: IssueTokenPair
    refresh: RefreshSession
    logout: EndSession
    delete_account: DeleteAccount
    change_phone: ChangePhone


def identity(db: Session) -> IdentityContext:
    accounts = SqlAccountRepository(db)
    challenges = SqlSliderChallengeRepository(db)
    codes = SqlCodeStore(db)
    codec = JwtTokenCodec()
    blacklist = SqlTokenBlacklist(db)
    sessions = SqlLoginSessionStore(db)
    rng = random.Random()

    issue_tokens = IssueTokenPair(
        codec,
        None,
        utcnow,
        settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        settings.REFRESH_TOKEN_EXPIRE_DAYS,
    )
    verify_code = VerifySmsCode(codes, utcnow, settings.SMS_MOCK_CODE if settings.SMS_MOCK_ENABLED else None)
    return IdentityContext(
        issue_captcha=IssueSliderChallenge(challenges, PilChallengeRenderer(), rng, utcnow),
        verify_captcha=VerifySliderChallenge(challenges, None, utcnow),
        consume_ticket=ConsumeSliderTicket(challenges, utcnow),
        send_code=SendVerificationCode(
            codes,
            ProviderSmsGateway(),
            ConsumeSliderTicket(challenges, utcnow) if settings.CAPTCHA_ENFORCED else None,
            rng,
            utcnow,
            mock_code=settings.SMS_MOCK_CODE if settings.SMS_MOCK_ENABLED else None,
        ),
        verify_code=verify_code,
        authenticate=AuthenticateBySms(accounts, verify_code, utcnow()),
        issue_tokens=issue_tokens,
        refresh=RefreshSession(codec, blacklist, accounts, issue_tokens, utcnow),
        logout=EndSession(codec, blacklist, sessions, utcnow),
        delete_account=DeleteAccount(codec, blacklist, sessions, accounts, utcnow),
        change_phone=ChangePhone(verify_code, accounts),
    )
