from __future__ import annotations

import pytest

from scripts.mail_dedupe import decide_mail_send, execute_with_update_on_success


def test_first_send_allowed_when_previous_hash_missing() -> None:
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash=None,
        previous_version=None,
    )
    assert decision.should_send is True
    assert decision.is_update_mail is False
    assert decision.new_version == 1


def test_skip_when_same_hash() -> None:
    initial = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash=None,
        previous_version=None,
    )
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash=initial.new_hash,
        previous_version=1,
    )
    assert decision.should_send is False
    assert decision.hash_changed is False
    assert decision.new_version == 1


def test_resend_when_hash_changed() -> None:
    initial = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash=None,
        previous_version=None,
    )
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="updated body",
        previous_hash=initial.new_hash,
        previous_version=1,
    )
    assert decision.should_send is True
    assert decision.is_update_mail is True


def test_version_increment_on_update_send() -> None:
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="updated body",
        previous_hash="old-hash",
        previous_version=3,
    )
    assert decision.should_send is True
    assert decision.new_version == 4


def test_no_metadata_update_when_send_fails() -> None:
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash=None,
        previous_version=None,
    )
    updated = {"called": False}

    with pytest.raises(RuntimeError):
        execute_with_update_on_success(
            decision=decision,
            send_action=lambda: (_ for _ in ()).throw(RuntimeError("send failed")),
            on_send_success=lambda: updated.__setitem__("called", True),
        )

    assert updated["called"] is False


def test_works_even_when_previous_hash_empty_string() -> None:
    decision = decide_mail_send(
        subject="Daily Report 2026-04-27",
        body="body",
        previous_hash="",
        previous_version=0,
    )
    assert decision.should_send is True
    assert decision.new_version == 1
