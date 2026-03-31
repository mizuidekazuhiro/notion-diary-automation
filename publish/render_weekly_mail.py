from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from publish.weekly_email_templates import render_weekly_html, render_weekly_text
from publish.weekly_graphs import GraphImage


@dataclass(frozen=True)
class WeeklyMailContent:
    subject: str
    plain_text: str
    html_body: str
    inline_images: list[GraphImage]


def render_weekly_mail(*, payload: dict[str, Any], graphs: list[GraphImage]) -> WeeklyMailContent:
    text = render_weekly_text(payload)
    html = render_weekly_html(
        {
            **payload,
            "graph_blocks": [(graph.cid, graph.alt) for graph in graphs],
        }
    )
    return WeeklyMailContent(
        subject=f"Weekly Report | {payload['period_label']}",
        plain_text=text,
        html_body=html,
        inline_images=graphs,
    )
