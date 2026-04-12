import importlib.util
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_TS = ROOT / "workers" / "src" / "index.ts"
DEFAULT_RELATIONS_TS = ROOT / "workers" / "src" / "application" / "daily_log_task_relations.ts"


def assert_contains(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def assert_not_contains(pattern: str, text: str, message: str) -> None:
    if re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def is_strict_mode() -> bool:
    return os.environ.get("CI_STRICT") == "1" or os.environ.get("GITHUB_ACTIONS") == "true"


def resolve_relations_ts_path() -> Path | None:
    env_path = os.environ.get("RELATIONS_TS_PATH")
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.exists():
            print(f"INFO: using RELATIONS_TS_PATH={candidate}")
            return candidate
        print(f"WARNING: RELATIONS_TS_PATH not found: {candidate}")

    if DEFAULT_RELATIONS_TS.exists():
        return DEFAULT_RELATIONS_TS

    return None


def main() -> None:
    index_text = INDEX_TS.read_text(encoding="utf-8")
    relations_ts_path = resolve_relations_ts_path()

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

    if relations_ts_path is None:
        message = "Daily log relation source not found; relation checks skipped."
        if is_strict_mode():
            raise AssertionError(message)
        print(f"WARNING: {message}")
    else:
        relations_text = relations_ts_path.read_text(encoding="utf-8")
        assert_contains(
            r"property:\s*dateProperty\s*,\s*date:\s*\{\s*is_not_empty:\s*true",
            relations_text,
            "Daily log relation query should require date is_not_empty.",
        )
        assert_contains(
            r"property:\s*dateProperty\s*,\s*date:\s*\{\s*on_or_after:",
            relations_text,
            "Daily log relation query should include on_or_after range.",
        )
        assert_contains(
            r"property:\s*dateProperty\s*,\s*date:\s*\{\s*before:",
            relations_text,
            "Daily log relation query should include before range.",
        )

    for dep in ["pandas", "numpy", "sklearn", "lightgbm"]:
        if importlib.util.find_spec(dep) is None:
            raise AssertionError(f"Required dependency is missing: {dep}")

    print(f"All requirement checks passed. strict_mode={is_strict_mode()}")


if __name__ == "__main__":
    main()
