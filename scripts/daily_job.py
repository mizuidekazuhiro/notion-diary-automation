import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from journal.daily_job import (
    ClosedTaskItem,
    ClosedTasks,
    Config,
    InboxItem,
    TaskItem,
    build_activity_summary,
    build_email_message,
    days_since,
    dedupe_closed_items,
    fetch_closed_tasks_safe,
    fetch_json,
    format_closed_items,
    load_config,
    main,
    parse_closed_tasks,
    parse_inbox,
    parse_tasks,
    post_json,
    send_email,
)

if __name__ == "__main__":
    main()
