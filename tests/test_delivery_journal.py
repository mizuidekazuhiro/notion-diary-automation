from concurrent.futures import ThreadPoolExecutor
import pytest


def test_claim_survives_reexecution_even_without_notion_metadata(tmp_path):
    from scripts.delivery_journal import DeliveryJournal
    first = DeliveryJournal(directory=tmp_path)
    assert first.claim("a" * 64, "b" * 64)
    # SMTP may have accepted this message before the process lost its receipt.
    assert not DeliveryJournal(directory=tmp_path).claim("a" * 64, "b" * 64)
    first.finish("sent")
    retry = DeliveryJournal(directory=tmp_path)
    assert not retry.claim("a" * 64, "b" * 64)
    assert retry.previous_state["state"] == "sent"


def test_concurrent_claim_only_one_wins(tmp_path):
    from scripts.delivery_journal import DeliveryJournal
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: DeliveryJournal(directory=tmp_path).claim("a" * 64, "b" * 64), range(2)))
    assert sorted(results) == [False, True]


def test_journal_failure_prevents_claim_and_does_not_hide_error(tmp_path):
    from scripts.delivery_journal import DeliveryJournal
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    with pytest.raises(OSError):
        DeliveryJournal(directory=blocked).claim("a" * 64, "b" * 64)
