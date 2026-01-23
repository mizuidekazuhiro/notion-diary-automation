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
