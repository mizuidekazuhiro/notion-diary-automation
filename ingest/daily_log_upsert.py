from typing import Any, Dict, Optional

from ingest.http_client import post_json


def upsert_daily_log(
    url: str, payload: Dict[str, Any], bearer_token: Optional[str]
) -> Dict[str, Any]:
    return post_json(url, payload, bearer_token)
