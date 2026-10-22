"""Aliyun SMS delivery shared by verification codes and admin notifications."""
import logging

from app.core.config import settings
from app.core.utils import mask_phone

logger = logging.getLogger(__name__)


class SmsService:
    """Thin wrapper around the Aliyun DysSMS client.

    ``send`` raises on failure on purpose: callers decide whether a failed SMS
    is fatal (verification code -> 502 to the caller) or non-fatal (member
    approval notice -> log and keep the approval). The Aliyun import stays
    lazy so environments without the SDK can still import this module.
    """

    @staticmethod
    def send(phone: str, template_id: str, template_param: str = "{}") -> None:
        if not template_id:
            raise RuntimeError("短信模板 ID 未配置")
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525.models import SendSmsRequest
        from alibabacloud_tea_openapi.models import Config

        cfg = Config(
            access_key_id=settings.SMS_ACCESS_KEY,
            access_key_secret=settings.SMS_ACCESS_KEY_SECRET,
            endpoint="dysmsapi.aliyuncs.com",
        )
        client = Client(cfg)
        req = SendSmsRequest(
            phone_numbers=phone,
            sign_name=settings.SMS_SIGN_NAME,
            template_code=template_id,
            template_param=template_param,
        )
        resp = client.send_sms(req)
        if resp.body.code != "OK":
            raise RuntimeError(f"阿里云短信发送失败: {resp.body.message}")

    @classmethod
    def send_verification_code(cls, phone: str, code: str) -> None:
        cls.send(phone, settings.SMS_TEMPLATE_ID, f'{{"code":"{code}"}}')

    @classmethod
    def send_member_approved(cls, phone: str) -> bool:
        """Notify an applicant that their membership was approved.

        Returns True only when the SMS provider accepted the notice. Mock mode
        and missing templates return False so the admin UI never claims a
        message was delivered. Real send errors propagate to the caller.
        """
        if settings.SMS_MOCK_ENABLED:
            logger.info("SMS mock mode: skip member-approved notice to %s", mask_phone(phone))
            return False
        if not settings.SMS_APPROVAL_TEMPLATE_ID:
            logger.warning("SMS_APPROVAL_TEMPLATE_ID not set; cannot notify member approval to %s", mask_phone(phone))
            return False
        cls.send(phone, settings.SMS_APPROVAL_TEMPLATE_ID)
        return True


sms_service = SmsService()
