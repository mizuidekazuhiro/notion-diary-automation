import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_TS = ROOT / "workers" / "src" / "index.ts"
RELATIONS_TS = ROOT / "workers" / "src" / "daily_log_task_relations.ts"


def assert_contains(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def assert_not_contains(pattern: str, text: str, message: str) -> None:
    if re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def main() -> None:
    index_text = INDEX_TS.read_text(encoding="utf-8")
    relations_text = RELATIONS_TS.read_text(encoding="utf-8")

    assert_not_contains(
        r'name:\s*"Notes"',
        index_text,
        "Daily_Log schema should not include Notes.",
    )
    assert_not_contains(
        r"properties\.Notes",
        index_text,
        "Daily_Log upsert payload should not include Notes.",
    )

    assert_contains(
        r'property:\s*(?:"Status"|TASK_STATUS_PROPERTY)\s*,\s*select:\s*\{\s*equals:\s*doneStatus',
        index_text,
        "Done filter should include Status == doneStatus.",
    )
    assert_contains(
        r'property:\s*(?:"Status"|TASK_STATUS_PROPERTY)\s*,\s*select:\s*\{\s*equals:\s*droppedStatus',
        index_text,
        "Drop filter should include Status == droppedStatus.",
    )
    assert_contains(
        r'property:\s*(?:"Done date"|TASK_DONE_DATE_PROPERTY)\s*,\s*date:\s*\{\s*is_not_empty:\s*true',
        index_text,
        "Done filter should require Done date is_not_empty.",
    )
    assert_contains(
        r'property:\s*(?:"Drop date"|TASK_DROP_DATE_PROPERTY)\s*,\s*date:\s*\{\s*is_not_empty:\s*true',
        index_text,
        "Drop filter should require Drop date is_not_empty.",
    )
    assert_contains(
        r'property:\s*(?:"Done date"|TASK_DONE_DATE_PROPERTY)[\s\S]*on_or_after:[\s\S]*before:',
        index_text,
        "Done filter should include date range.",
    )
    assert_contains(
        r'property:\s*(?:"Drop date"|TASK_DROP_DATE_PROPERTY)[\s\S]*on_or_after:[\s\S]*before:',
        index_text,
        "Drop filter should include date range.",
    )

    assert_contains(
        r"dateProperty[\s\S]*is_not_empty",
        relations_text,
        "Daily log relation query should require date is_not_empty.",
    )

    print("All requirement checks passed.")


if __name__ == "__main__":
    main()
