"""Chinese fallback messages and HTTP mapping for authentication outcomes."""

from app.domain.auth import BizCode
from app.membership.domain.application import MemberCode


class ErrorCode:
    """Single Chinese fallback catalog and HTTP mapping for auth outcomes."""

    _messages = {
        BizCode.OK: "操作成功",
        BizCode.AUTH_SMS_SENT: "验证码已发送",
        BizCode.AUTH_LOGIN_SUCCESS: "登录成功",
        BizCode.AUTH_REGISTER_SUCCESS: "注册成功",
        BizCode.AUTH_ACCOUNT_CREATED: "首次登录，已为你创建账号",
        BizCode.AUTH_TOKEN_REFRESHED: "刷新成功",
        BizCode.AUTH_PROFILE_UPDATED: "资料已更新",
        BizCode.AUTH_LOGGED_OUT: "已登出",
        BizCode.AUTH_ACCOUNT_DELETED: "账号已注销",
        BizCode.AUTH_PHONE_CHANGED: "手机号已更新",
        BizCode.AUTH_INVALID_PHONE: "手机号格式不正确",
        BizCode.AUTH_INVALID_CODE: "验证码错误或已过期",
        BizCode.AUTH_USER_NOT_FOUND: "用户不存在，请先注册",
        BizCode.AUTH_USER_EXISTS: "用户已存在",
        BizCode.AUTH_PHONE_PREVIOUSLY_DELETED: "该手机号曾被删除，如需重新注册请联系管理员恢复",
        BizCode.AUTH_ACCOUNT_DISABLED: "账号已被禁用",
        BizCode.AUTH_TOKEN_ACCOUNT_DISABLED: "账号已被禁用",
        BizCode.AUTH_ACCOUNT_REMOVED: "账号已被删除",
        BizCode.AUTH_PHONE_EXISTS: "该手机号已被注册",
        BizCode.AUTH_SLIDER_FAILED: "滑块验证未通过，请重试",
        BizCode.AUTH_SLIDER_REQUIRED: "请先完成滑块验证",
        BizCode.AUTH_SMS_RATE_LIMIT: "验证码发送过于频繁，请 {remaining} 秒后再试",
        BizCode.AUTH_SMS_DAILY_LIMIT: "今日验证码发送次数已达上限（每日 {limit} 次），请明天再试",
        BizCode.AUTH_SMS_FAILED: "短信发送失败，请稍后重试",
        BizCode.AUTH_TOKEN_INVALID: "无效或过期的 token",
        BizCode.AUTH_REFRESH_INVALID: "无效 refresh token",
        BizCode.AUTH_REFRESH_REVOKED: "refresh token 已失效",
        BizCode.AUTH_ACCOUNT_UNAVAILABLE: "账号不可用",
        BizCode.AUTH_TOKEN_REVOKED: "Token 已被撤销",
        BizCode.AUTH_FORCED_LOGOUT: "Token 已被撤销（强制登出）",
    }
    _statuses = {
        BizCode.AUTH_INVALID_PHONE: 400,
        BizCode.AUTH_INVALID_CODE: 400,
        BizCode.AUTH_USER_NOT_FOUND: 400,
        BizCode.AUTH_USER_EXISTS: 409,
        BizCode.AUTH_PHONE_PREVIOUSLY_DELETED: 409,
        BizCode.AUTH_ACCOUNT_DISABLED: 403,
        BizCode.AUTH_TOKEN_ACCOUNT_DISABLED: 401,
        BizCode.AUTH_ACCOUNT_REMOVED: 403,
        BizCode.AUTH_PHONE_EXISTS: 409,
        BizCode.AUTH_SLIDER_FAILED: 400,
        BizCode.AUTH_SLIDER_REQUIRED: 400,
        BizCode.AUTH_SMS_RATE_LIMIT: 429,
        BizCode.AUTH_SMS_DAILY_LIMIT: 429,
        BizCode.AUTH_SMS_FAILED: 502,
        BizCode.AUTH_TOKEN_INVALID: 401,
        BizCode.AUTH_REFRESH_INVALID: 401,
        BizCode.AUTH_REFRESH_REVOKED: 401,
        BizCode.AUTH_ACCOUNT_UNAVAILABLE: 401,
        BizCode.AUTH_TOKEN_REVOKED: 401,
        BizCode.AUTH_FORCED_LOGOUT: 401,
    }

    @classmethod
    def message(cls, code: BizCode, **params: object) -> str:
        return cls._messages[code].format(**params)

    @classmethod
    def status(cls, code: BizCode) -> int:
        return cls._statuses[code]

    @classmethod
    def error_body(cls, code: BizCode, **params: object) -> dict:
        status = cls.status(code)
        body = {"code": status, "biz_code": code.value, "detail": cls.message(code, **params)}
        if params:
            body["params"] = params
        return body


class MemberMessages:
    """HTTP fallback catalog for membership outcomes."""

    _messages = {
        MemberCode.ALREADY_MEMBER: "当前账号已是会员或管理员，无需申请",
        MemberCode.APPLICATION_PENDING: "已有待审核的申请，请耐心等待",
        MemberCode.USER_NOT_FOUND: "用户不存在",
        MemberCode.APPLICATION_NOT_PENDING: "该申请已处理或不存在",
        MemberCode.APPLICATION_SUBMITTED: "申请已提交，等待审核",
        MemberCode.APPLICATION_REJECTED: "已驳回",
        MemberCode.APPROVED_NOTIFIED: "已通过，用户已升级为会员，短信通知已发送",
        MemberCode.APPROVED_UNNOTIFIED: "已通过，用户已升级为会员，短信通知未发出（详见服务日志）",
        MemberCode.INVALID_ROLE: "无效角色",
        MemberCode.USER_ALREADY_DELETED: "用户已删除",
        MemberCode.CANNOT_DELETE_SELF: "不能删除当前登录的管理员账号",
        MemberCode.ROLE_UPDATED: "操作成功",
        MemberCode.USER_BLACKLISTED: "已拉黑",
        MemberCode.USER_UNBLACKLISTED: "已解除拉黑",
        MemberCode.USER_LOGGED_OUT: "已强制登出",
        MemberCode.USER_DELETED: "已删除",
    }
    _statuses = {
        MemberCode.ALREADY_MEMBER: 400,
        MemberCode.APPLICATION_PENDING: 400,
        MemberCode.USER_NOT_FOUND: 404,
        MemberCode.APPLICATION_NOT_PENDING: 400,
        MemberCode.INVALID_ROLE: 400,
        MemberCode.USER_ALREADY_DELETED: 409,
        MemberCode.CANNOT_DELETE_SELF: 400,
    }

    @classmethod
    def message(cls, code: MemberCode) -> str:
        return cls._messages[code]

    @classmethod
    def status(cls, code: MemberCode) -> int:
        return cls._statuses[code]
