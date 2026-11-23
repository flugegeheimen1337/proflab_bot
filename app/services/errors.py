class DomainError(Exception):
    code = "error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.code
        super().__init__(self.detail)


class NotAMember(DomainError):
    code = "not_a_member"


class UserBanned(DomainError):
    code = "banned"


class UserErased(DomainError):
    code = "erased"


class RateLimited(DomainError):
    code = "rate_limited"


class QueueFull(DomainError):
    code = "queue_full"


class QueueUnavailable(DomainError):
    code = "queue_unavailable"


class ContentRejected(DomainError):
    code = "content"


class GroupUnavailable(DomainError):
    code = "group_unavailable"


class PermissionDenied(DomainError):
    code = "forbidden"


class NotFound(DomainError):
    code = "not_found"


class ReplyTargetMissing(DomainError):
    code = "reply_missing"


class AliasExhausted(DomainError):
    code = "alias_exhausted"


class ScheduleError(DomainError):
    code = "schedule"


class TemporaryTelegramError(Exception):
    def __init__(self, retry_after: float = 1.0) -> None:
        self.retry_after = retry_after
        super().__init__(f"retry after {retry_after}")


class BotBlocked(Exception):
    pass


class PermanentTelegramError(Exception):
    pass
