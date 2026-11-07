"""Conservative signals from explicit location descriptions; absence is unknown."""
import re


def extract_location_flags(text: str) -> dict[str, bool | None]:
    text = (text or "").lower()
    legacy = any(tag in text for tag in ("late_outing_day", "multi_stop_day", "home_heavy_day", "office_heavy_day", "outing_heavy_day"))
    rules = {
        "late_outing_flag": (r"late_outing_day|late outing|(?:深夜|夜遅|23時|23:)[^。\n]{0,30}(?:外出|移動|外に)|(?:外出|移動)[^。\n]{0,30}(?:深夜|夜遅|23時|23:)", r"深夜の外出(?:は)?なし|深夜外出なし"),
        "multi_stop_flag": (r"multi_stop_day|multi stop|複数(?:の)?(?:場所|地点)|はしご|立ち寄りが多", r"移動なし|終日在宅"),
        "home_heavy_flag": (r"home_heavy_day|home heavy|自宅中心|終日在宅", r"終日外出"),
        "office_heavy_flag": (r"office_heavy_day|office heavy|オフィス中心|終日出社", r"終日在宅"),
        "outing_heavy_flag": (r"outing_heavy_day|outing heavy|外出中心|終日外出", r"外出なし|終日在宅"),
    }
    return {key: False if re.search(negative, text) else True if re.search(positive, text) else False if legacy else None
            for key, (positive, negative) in rules.items()}
