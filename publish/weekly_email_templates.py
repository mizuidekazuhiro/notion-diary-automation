from __future__ import annotations

from typing import Any


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "データ不足"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def render_weekly_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Weekly Report | {payload['period_label']}")
    lines.append("")
    lines.append("1. 週の総括")
    lines.append(payload["llm_sections"].get("summary", "データ不足"))
    lines.append("")
    lines.append("2. 主要指標サマリー")
    for name, value in payload["key_metrics"].items():
        lines.append(f"- {name}: {_fmt(value)}")
    lines.append("")
    lines.append("3. グラフ5本")
    for g in payload["graph_descriptions"]:
        lines.append(f"- {g}")
    lines.append("")
    lines.append("4. 良かった点")
    lines.append(payload["llm_sections"].get("good_points", "データ不足"))
    lines.append("")
    lines.append("5. 注意点・異常検知")
    lines.append(payload["llm_sections"].get("alerts", "データ不足"))
    lines.append("")
    lines.append("6. パターン分析")
    lines.append(payload["llm_sections"].get("patterns", "データ不足"))
    lines.append("")
    lines.append("7. 来週の具体アクション")
    lines.append(payload["llm_sections"].get("actions", "データ不足"))
    lines.append("")
    lines.append("8. 日別ログ要約")
    lines.append(payload["llm_sections"].get("daily_digest", "データ不足"))
    return "\n".join(lines).strip() + "\n"


def render_weekly_html(payload: dict[str, Any]) -> str:
    graph_html = "".join(
        f'<div style="margin:12px 0"><div style="font-weight:600">{desc}</div><img alt="{desc}" src="cid:{cid}" style="max-width:100%;border:1px solid #ddd;border-radius:6px"/></div>'
        for cid, desc in payload["graph_blocks"]
    )
    return f"""
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;line-height:1.5">
<h2>Weekly Report | {payload['period_label']}</h2>
<h3>1. 週の総括</h3><p>{payload['llm_sections'].get('summary','データ不足')}</p>
<h3>2. 主要指標サマリー</h3>
<ul>{''.join(f'<li>{k}: {_fmt(v)}</li>' for k,v in payload['key_metrics'].items())}</ul>
<h3>3. グラフ5本</h3>
{graph_html}
<h3>4. 良かった点</h3><p>{payload['llm_sections'].get('good_points','データ不足')}</p>
<h3>5. 注意点・異常検知</h3><p>{payload['llm_sections'].get('alerts','データ不足')}</p>
<h3>6. パターン分析</h3><p>{payload['llm_sections'].get('patterns','データ不足')}</p>
<h3>7. 来週の具体アクション</h3><p>{payload['llm_sections'].get('actions','データ不足')}</p>
<h3>8. 日別ログ要約</h3><p>{payload['llm_sections'].get('daily_digest','データ不足')}</p>
</body></html>
""".strip()
