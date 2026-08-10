from __future__ import annotations

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
ANKI_CONNECT_URL = "http://127.0.0.1:8765"
ANKI_CONNECT_VERSION = 6
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_SESSION_GAP_MINUTES = 10
DEFAULT_MAX_ANSWER_SECONDS = 300
DEFAULT_CREDENTIAL_PATH = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
) / "AnkiNotionSync" / "config.json"
DEFAULT_LOG_DIR = DEFAULT_CREDENTIAL_PATH.parent / "logs"
ANKI_DAILY_PATH = "/execute/api/study/anki-daily"
WORKER_USER_AGENT = "AnkiNotionSync/1.0 (+Windows)"


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class Review:
    reviewed_at_ms: int
    duration_ms: int


@dataclass(frozen=True)
class DailyAggregate:
    target_date: str
    study_minutes: float
    study_sessions: int
    first_review_at: str | None
    last_review_at: str | None
    review_count: int
    max_time_review_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date,
            "study_minutes": self.study_minutes,
            "study_sessions": self.study_sessions,
            "first_review_at": self.first_review_at,
            "last_review_at": self.last_review_at,
            "review_count": self.review_count,
            "max_time_review_count": self.max_time_review_count,
            "source": "anki_revlog",
        }


@dataclass(frozen=True)
class RuntimeConfig:
    worker_url: str
    worker_token: str
    anki_connect_key: str | None
    anki_executable: str | None
    profile: str | None
    backfill_days: int
    session_gap_minutes: int
    max_answer_seconds: int


def _positive_int(value: Any, *, name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SyncError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise SyncError(f"{name} must be a positive integer")
    return parsed


def normalize_worker_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise SyncError("Worker URL is missing (ANKI_NOTION_WORKER_URL or config.worker_url)")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SyncError("Worker URL must be an http(s) URL")
    path = parsed.path.rstrip("/")
    if not path.endswith(ANKI_DAILY_PATH):
        path = ANKI_DAILY_PATH
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def decrypt_dpapi_hex(value: str) -> str:
    if os.name != "nt":
        raise SyncError("DPAPI credentials can only be decrypted on Windows")
    try:
        encrypted = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise SyncError("DPAPI credential is not valid hexadecimal data") from exc
    buffer = ctypes.create_string_buffer(encrypted)
    in_blob = _DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise SyncError("Windows could not decrypt the saved credential for this user")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return raw.decode("utf-16-le").rstrip("\x00")


def _read_optional_secret(data: dict[str, Any], env_name: str, config_name: str) -> str | None:
    environment_value = os.environ.get(env_name, "").strip()
    if environment_value:
        return environment_value
    encrypted = str(data.get(config_name, "") or "").strip()
    return decrypt_dpapi_hex(encrypted) if encrypted else None


def load_runtime_config(path: Path, *, require_worker: bool = True) -> RuntimeConfig:
    data: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncError(f"Could not read config: {path}") from exc
        if not isinstance(parsed, dict):
            raise SyncError("Config root must be a JSON object")
        data = parsed

    worker_token = _read_optional_secret(
        data, "WORKERS_BEARER_TOKEN", "worker_token_dpapi"
    )
    if require_worker and not worker_token:
        raise SyncError(
            "Worker token is missing. Run install_anki_sync_task.ps1 once or set WORKERS_BEARER_TOKEN."
        )
    raw_worker_url = (
        os.environ.get("ANKI_NOTION_WORKER_URL", "")
        or os.environ.get("DAILY_LOG_UPSERT_URL", "")
        or str(data.get("worker_url", ""))
    )
    worker_url = normalize_worker_url(raw_worker_url) if raw_worker_url else ""
    if require_worker and not worker_url:
        raise SyncError("Worker URL is missing (ANKI_NOTION_WORKER_URL or config.worker_url)")
    return RuntimeConfig(
        worker_url=worker_url,
        worker_token=worker_token or "",
        anki_connect_key=_read_optional_secret(
            data, "ANKI_CONNECT_KEY", "anki_connect_key_dpapi"
        ),
        anki_executable=(
            os.environ.get("ANKI_EXECUTABLE", "").strip()
            or str(data.get("anki_executable", "") or "").strip()
            or None
        ),
        profile=(
            os.environ.get("ANKI_PROFILE", "").strip()
            or str(data.get("profile", "") or "").strip()
            or None
        ),
        backfill_days=_positive_int(
            os.environ.get("ANKI_BACKFILL_DAYS", data.get("backfill_days")),
            name="backfill_days",
            default=DEFAULT_BACKFILL_DAYS,
        ),
        session_gap_minutes=_positive_int(
            os.environ.get("ANKI_SESSION_GAP_MINUTES", data.get("session_gap_minutes")),
            name="session_gap_minutes",
            default=DEFAULT_SESSION_GAP_MINUTES,
        ),
        max_answer_seconds=_positive_int(
            os.environ.get("ANKI_MAX_ANSWER_SECONDS", data.get("max_answer_seconds")),
            name="max_answer_seconds",
            default=DEFAULT_MAX_ANSWER_SECONDS,
        ),
    )


def configure_logging(log_dir: Path, *, verbose: bool = False) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "anki_revlog_sync.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    return log_path


class AnkiConnectClient:
    def __init__(self, *, key: str | None = None, timeout_seconds: int = 300):
        self.key = key
        self.timeout_seconds = timeout_seconds

    def invoke(self, action: str, params: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {
            "action": action,
            "version": ANKI_CONNECT_VERSION,
            "params": params or {},
        }
        if self.key:
            payload["key"] = self.key
        request = Request(
            ANKI_CONNECT_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.load(response)
        except (URLError, TimeoutError, OSError) as exc:
            raise SyncError(
                "AnkiConnect is unavailable. Start Anki and install/enable AnkiConnect on localhost:8765."
            ) from exc
        if not isinstance(result, dict) or "result" not in result or "error" not in result:
            raise SyncError("AnkiConnect returned an unexpected response")
        if result["error"] is not None:
            raise SyncError(f"AnkiConnect {action} failed: {result['error']}")
        return result["result"]

    def check(self) -> int:
        version = self.invoke("version")
        if not isinstance(version, int) or version < ANKI_CONNECT_VERSION:
            raise SyncError(
                f"AnkiConnect API v{ANKI_CONNECT_VERSION}+ is required; reported {version!r}"
            )
        reflected = self.invoke(
            "apiReflect",
            {
                "scopes": ["actions"],
                "actions": ["sync", "cardReviews", "deckNames", "getActiveProfile"],
            },
        )
        actions = set(reflected.get("actions", [])) if isinstance(reflected, dict) else set()
        missing = {"sync", "cardReviews", "deckNames", "getActiveProfile"} - actions
        if missing:
            raise SyncError(f"AnkiConnect is missing required actions: {', '.join(sorted(missing))}")
        return version


def find_anki_executable(configured: str | None = None) -> Path:
    candidates = [
        Path(configured) if configured else None,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Anki" / "anki.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Anki" / "anki.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Anki" / "anki.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise SyncError("Anki executable was not found; set ANKI_EXECUTABLE or config.anki_executable")


def ensure_anki_available(
    client: AnkiConnectClient,
    *,
    start_anki: bool,
    executable: str | None,
    wait_seconds: int = 120,
) -> int:
    try:
        return client.check()
    except SyncError:
        if not start_anki:
            raise
    anki_executable = find_anki_executable(executable)
    logging.info("AnkiConnect unavailable; starting Anki executable=%s", anki_executable)
    subprocess.Popen([str(anki_executable)], cwd=str(anki_executable.parent))
    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            return client.check()
        except SyncError as exc:
            last_error = exc
    raise SyncError(f"AnkiConnect did not become available within {wait_seconds}s") from last_error


def study_window(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, datetime_time(4, 0), tzinfo=JST)
    return start, start + timedelta(days=1)


def resolve_target_date(reviewed_at_ms: int) -> date:
    reviewed = datetime.fromtimestamp(reviewed_at_ms / 1000, tz=JST)
    return (reviewed - timedelta(hours=4)).date()


def target_dates_for_backfill(now: datetime, days: int) -> list[date]:
    current = (now.astimezone(JST) - timedelta(hours=4)).date()
    return [current - timedelta(days=offset) for offset in reversed(range(days))]


def parse_card_reviews(rows: Iterable[Any]) -> list[Review]:
    by_id: dict[int, Review] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            raise SyncError("AnkiConnect cardReviews returned an invalid row")
        try:
            reviewed_at_ms = int(row[0])
            duration_ms = max(0, int(row[7]))
        except (TypeError, ValueError) as exc:
            raise SyncError("AnkiConnect cardReviews returned non-numeric values") from exc
        by_id[reviewed_at_ms] = Review(reviewed_at_ms, duration_ms)
    return sorted(by_id.values(), key=lambda item: item.reviewed_at_ms)


def collect_reviews_via_anki_connect(
    client: AnkiConnectClient, *, start_ms: int
) -> list[Review]:
    deck_names = client.invoke("deckNames")
    if not isinstance(deck_names, list) or not all(isinstance(name, str) for name in deck_names):
        raise SyncError("AnkiConnect deckNames returned an unexpected value")
    rows: list[Any] = []
    for deck_name in deck_names:
        deck_rows = client.invoke("cardReviews", {"deck": deck_name, "startID": start_ms})
        if not isinstance(deck_rows, list):
            raise SyncError(f"AnkiConnect cardReviews returned an invalid result for deck {deck_name!r}")
        rows.extend(deck_rows)
    reviews = parse_card_reviews(rows)
    logging.info(
        "AnkiConnect review collection complete decks=%s raw_rows=%s unique_reviews=%s",
        len(deck_names),
        len(rows),
        len(reviews),
    )
    return reviews


def collection_path(profile: str) -> Path:
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    return appdata / "Anki2" / profile / "collection.anki2"


def collect_reviews_via_sqlite_backup(
    *, profile: str, start_ms: int, end_ms: int
) -> list[Review]:
    path = collection_path(profile)
    if not path.is_file():
        raise SyncError(f"Anki collection was not found for profile {profile!r}: {path}")
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        rows = snapshot.execute(
            "SELECT id, time FROM revlog WHERE id >= ? AND id < ? ORDER BY id",
            (start_ms, end_ms),
        ).fetchall()
    except sqlite3.Error as exc:
        raise SyncError(f"Could not read a consistent Anki SQLite snapshot: {exc}") from exc
    finally:
        snapshot.close()
        source.close()
    reviews = [Review(int(row[0]), max(0, int(row[1]))) for row in rows]
    logging.info("SQLite backup fallback complete reviews=%s profile=%s", len(reviews), profile)
    return reviews


def aggregate_reviews(
    reviews: Sequence[Review],
    target_date: date,
    *,
    session_gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
    max_answer_seconds: int = DEFAULT_MAX_ANSWER_SECONDS,
) -> DailyAggregate:
    start, end = study_window(target_date)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    selected = sorted(
        (review for review in reviews if start_ms <= review.reviewed_at_ms < end_ms),
        key=lambda item: item.reviewed_at_ms,
    )
    session_gap_ms = session_gap_minutes * 60 * 1000
    sessions = 0
    previous_ms: int | None = None
    for review in selected:
        if previous_ms is None or review.reviewed_at_ms - previous_ms >= session_gap_ms:
            sessions += 1
        previous_ms = review.reviewed_at_ms
    max_time_count = sum(
        review.duration_ms >= max_answer_seconds * 1000 for review in selected
    )

    def as_iso(review: Review | None) -> str | None:
        if review is None:
            return None
        return datetime.fromtimestamp(review.reviewed_at_ms / 1000, tz=JST).isoformat(
            timespec="milliseconds"
        )

    return DailyAggregate(
        target_date=target_date.isoformat(),
        study_minutes=round(sum(review.duration_ms for review in selected) / 60_000, 2),
        study_sessions=sessions,
        first_review_at=as_iso(selected[0] if selected else None),
        last_review_at=as_iso(selected[-1] if selected else None),
        review_count=len(selected),
        max_time_review_count=max_time_count,
    )


def post_aggregate(config: RuntimeConfig, aggregate: DailyAggregate) -> dict[str, Any]:
    request = Request(
        config.worker_url,
        data=json.dumps(aggregate.to_payload(), ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.worker_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": WORKER_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SyncError(f"Worker rejected {aggregate.target_date}: HTTP {exc.code} {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SyncError(f"Worker request failed for {aggregate.target_date}: {exc}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SyncError(f"Worker returned an invalid response for {aggregate.target_date}")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Anki revlog study totals to Notion Daily Log")
    parser.add_argument("--config", type=Path, default=DEFAULT_CREDENTIAL_PATH)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--target-date", action="append", default=[])
    parser.add_argument("--backfill-days", type=int)
    parser.add_argument("--start-anki", action="store_true")
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    log_path = configure_logging(args.log_dir, verbose=args.verbose)
    logging.info("Anki revlog sync started log=%s", log_path)
    config = load_runtime_config(args.config, require_worker=not args.dry_run)
    client = AnkiConnectClient(key=config.anki_connect_key)
    version = ensure_anki_available(
        client,
        start_anki=args.start_anki,
        executable=config.anki_executable,
    )
    active_profile = client.invoke("getActiveProfile")
    if not isinstance(active_profile, str) or not active_profile:
        raise SyncError("AnkiConnect did not return an active profile")
    if config.profile and config.profile != active_profile:
        raise SyncError(
            f"Configured profile {config.profile!r} is not active; active profile is {active_profile!r}"
        )
    logging.info("Anki ready api_version=%s active_profile=%s", version, active_profile)
    if args.doctor:
        logging.info("Doctor checks passed worker_host=%s", urlsplit(config.worker_url).netloc)
        return 0
    if not args.no_sync:
        logging.info("Starting AnkiWeb sync")
        client.invoke("sync")
        client.check()
        logging.info("AnkiWeb sync completed")

    if args.target_date:
        try:
            target_dates = sorted({date.fromisoformat(value) for value in args.target_date})
        except ValueError as exc:
            raise SyncError("--target-date must be YYYY-MM-DD") from exc
    else:
        backfill_days = args.backfill_days or config.backfill_days
        if backfill_days < 1:
            raise SyncError("--backfill-days must be a positive integer")
        target_dates = target_dates_for_backfill(datetime.now(JST), backfill_days)
    earliest_start, _ = study_window(target_dates[0])
    _, latest_end = study_window(target_dates[-1])
    start_ms = int(earliest_start.timestamp() * 1000)
    end_ms = int(latest_end.timestamp() * 1000)
    try:
        reviews = collect_reviews_via_anki_connect(client, start_ms=start_ms - 1)
        source = "ankiconnect"
    except SyncError as exc:
        logging.warning("AnkiConnect review read failed; using SQLite backup fallback error=%s", exc)
        reviews = collect_reviews_via_sqlite_backup(
            profile=active_profile, start_ms=start_ms, end_ms=end_ms
        )
        source = "sqlite_backup"

    failures = 0
    for target_date in target_dates:
        aggregate = aggregate_reviews(
            reviews,
            target_date,
            session_gap_minutes=config.session_gap_minutes,
            max_answer_seconds=config.max_answer_seconds,
        )
        rate = (
            aggregate.max_time_review_count / aggregate.review_count
            if aggregate.review_count
            else 0
        )
        logging.info(
            "Anki aggregate target_date=%s source=%s minutes=%.2f sessions=%s reviews=%s max_answer_seconds=%s max_time_count=%s max_time_rate=%.4f",
            aggregate.target_date,
            source,
            aggregate.study_minutes,
            aggregate.study_sessions,
            aggregate.review_count,
            config.max_answer_seconds,
            aggregate.max_time_review_count,
            rate,
        )
        if args.dry_run:
            continue
        try:
            result = post_aggregate(config, aggregate)
            logging.info(
                "Worker upsert complete target_date=%s created=%s updated=%s daily_log_updated=%s",
                aggregate.target_date,
                result.get("created"),
                result.get("updated"),
                result.get("daily_log_updated"),
            )
        except SyncError:
            failures += 1
            logging.exception("Worker upsert failed target_date=%s", aggregate.target_date)
    if failures:
        raise SyncError(f"{failures} target date(s) failed; the next run will backfill them")
    logging.info("Anki revlog sync completed target_dates=%s", len(target_dates))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except SyncError as exc:
        logging.error("Anki revlog sync failed: %s", exc)
        return 1
    except Exception:
        logging.exception("Anki revlog sync failed unexpectedly")
        return 1


if __name__ == "__main__":
    sys.exit(main())
