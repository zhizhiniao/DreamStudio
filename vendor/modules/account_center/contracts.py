"""Provider-neutral seams for future authorized analytics/comment integrations.

No imports from publishing sessions, database writes or outbound requests occur here.
"""
from dataclasses import dataclass
from typing import Protocol

PLATFORMS = {
    'douyin': ('抖音', 'planned'),
    'kuaishou': ('快手', 'planned'),
    'wechat_channels': ('视频号', 'reserved'),
    'xiaohongshu': ('小红书', 'reserved'),
    'bilibili': ('哔哩哔哩', 'reserved'),
}
CAPABILITIES = ('analytics', 'works', 'comments', 'reply_comment', 'send_dm', 'verify_delivery')


@dataclass(frozen=True)
class AccountScope:
    platform: str
    account_id: str

    def __post_init__(self):
        if self.platform not in PLATFORMS:
            raise ValueError('unknown_platform')
        if not self.account_id or not self.account_id.strip():
            raise ValueError('account_id_required')

    def entity_key(self, kind: str, entity_id: str) -> tuple[str, str, str, str]:
        """Opaque platform IDs must never be globally unique on their own."""
        if not kind or not entity_id:
            raise ValueError('entity_identity_required')
        return self.platform, self.account_id, kind, entity_id


@dataclass(frozen=True)
class ReplyRequest:
    scope: AccountScope
    work_id: str
    comment_id: str
    text: str
    idempotency_key: str
    channel: str = 'comment'

    def __post_init__(self):
        if not all(v and v.strip() for v in (self.work_id, self.comment_id, self.text, self.idempotency_key)):
            raise ValueError('reply_fields_required')
        if self.channel not in ('comment', 'dm'):
            raise ValueError('invalid_reply_channel')


class AccountAdapter(Protocol):
    def capabilities(self, scope: AccountScope) -> dict[str, str]: ...
    def list_works(self, scope: AccountScope, cursor: str | None = None) -> dict: ...
    def analytics(self, scope: AccountScope, start: str, end: str) -> dict: ...
    def list_comments(self, scope: AccountScope, work_id: str, cursor: str | None = None) -> dict: ...
    def send_reply(self, request: ReplyRequest) -> dict: ...
    def delivery(self, scope: AccountScope, operation_id: str) -> dict: ...


class AdapterUnavailable(RuntimeError):
    code = 'adapter_not_configured'
    retryable = False


class PendingAdapter:
    def __init__(self, platform: str):
        self.platform = platform

    def _check(self, scope: AccountScope):
        if scope.platform != self.platform:
            raise ValueError('platform_scope_mismatch')

    def capabilities(self, scope: AccountScope) -> dict[str, str]:
        self._check(scope)
        return dict.fromkeys(CAPABILITIES, 'not_configured')

    def _unavailable(self, scope: AccountScope):
        self._check(scope)
        raise AdapterUnavailable(self.platform)

    def list_works(self, scope, cursor=None):
        return self._unavailable(scope)

    def analytics(self, scope, start, end):
        return self._unavailable(scope)

    def list_comments(self, scope, work_id, cursor=None):
        return self._unavailable(scope)

    def send_reply(self, request: ReplyRequest):
        return self._unavailable(request.scope)

    def delivery(self, scope, operation_id):
        return self._unavailable(scope)


def get_adapter(platform: str) -> AccountAdapter:
    if platform not in PLATFORMS:
        raise ValueError('unknown_platform')
    return PendingAdapter(platform)


def platform_catalog() -> list[dict]:
    return [dict(id=k, name=v[0], stage=v[1], capabilities=dict.fromkeys(CAPABILITIES, 'not_configured'))
            for k, v in PLATFORMS.items()]
