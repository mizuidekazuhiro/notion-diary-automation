from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

UPDATE_SUBJECT_PREFIX = "【更新版】"


@dataclass(frozen=True)
class MailDedupeDecision:
    previous_hash: str | None
    new_hash: str
    hash_changed: bool
    should_send: bool
    is_update_mail: bool
    previous_version: int
    new_version: int

    def apply_subject_prefix(self, subject: str) -> str:
        if not self.is_update_mail:
            return subject
        if subject.startswith(UPDATE_SUBJECT_PREFIX):
            return subject
        return f"{UPDATE_SUBJECT_PREFIX}{subject}"


def build_hash_payload(subject: str, body: str) -> str:
    return f"{subject}\n\n{body}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_version(version: int | None) -> int:
    if not isinstance(version, int):
        return 0
    if version < 0:
        return 0
    return version


def decide_mail_send(
    *,
    subject: str,
    body: str,
    previous_hash: str | None,
    previous_version: int | None,
) -> MailDedupeDecision:
    normalized_previous_hash = (previous_hash or "").strip() or None
    normalized_previous_version = _normalize_version(previous_version)
    new_hash = sha256_hex(build_hash_payload(subject, body))

    if not normalized_previous_hash:
        return MailDedupeDecision(
            previous_hash=None,
            new_hash=new_hash,
            hash_changed=True,
            should_send=True,
            is_update_mail=False,
            previous_version=normalized_previous_version,
            new_version=normalized_previous_version + 1 if normalized_previous_version > 0 else 1,
        )

    if normalized_previous_hash == new_hash:
        return MailDedupeDecision(
            previous_hash=normalized_previous_hash,
            new_hash=new_hash,
            hash_changed=False,
            should_send=False,
            is_update_mail=False,
            previous_version=normalized_previous_version,
            new_version=normalized_previous_version,
        )

    return MailDedupeDecision(
        previous_hash=normalized_previous_hash,
        new_hash=new_hash,
        hash_changed=True,
        should_send=True,
        is_update_mail=True,
        previous_version=normalized_previous_version,
        new_version=max(normalized_previous_version + 1, 2),
    )


def execute_with_update_on_success(
    *,
    decision: MailDedupeDecision,
    send_action: Callable[[], None],
    on_send_success: Callable[[], None],
) -> bool:
    if not decision.should_send:
        return False
    send_action()
    on_send_success()
    return True
