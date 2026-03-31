from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Sequence

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    matplotlib = None
    plt = None


@dataclass(frozen=True)
class GraphImage:
    cid: str
    filename: str
    data: bytes
    alt: str


def _to_png_bytes(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=140)
    plt.close(fig)
    return buffer.getvalue()


def _build_line_graph(
    *,
    cid: str,
    filename: str,
    title: str,
    labels: Sequence[str],
    primary: Sequence[Optional[float]],
    primary_label: str,
    secondary: Sequence[Optional[float]] | None = None,
    secondary_label: str | None = None,
    threshold: Optional[float] = None,
) -> GraphImage:
    if plt is None:
        raise RuntimeError("matplotlib is required for weekly graph rendering")
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    ax.plot(labels, primary, marker="o", linewidth=2, label=primary_label)
    if secondary is not None and secondary_label:
        ax.plot(labels, secondary, marker="o", linewidth=1.6, label=secondary_label)
    if threshold is not None:
        ax.axhline(threshold, color="#d97706", linestyle="--", linewidth=1.2)
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    return GraphImage(cid=cid, filename=filename, data=_to_png_bytes(fig), alt=title)


def _build_bar_graph(
    *,
    cid: str,
    filename: str,
    title: str,
    labels: Sequence[str],
    series_a: Sequence[Optional[float]],
    label_a: str,
    series_b: Sequence[Optional[float]],
    label_b: str,
) -> GraphImage:
    if plt is None:
        raise RuntimeError("matplotlib is required for weekly graph rendering")
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    x = range(len(labels))
    width = 0.36
    ax.bar([v - width / 2 for v in x], [v or 0 for v in series_a], width=width, label=label_a)
    ax.bar([v + width / 2 for v in x], [v or 0 for v in series_b], width=width, label=label_b)
    ax.set_xticks(list(x), labels)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(loc="best")
    return GraphImage(cid=cid, filename=filename, data=_to_png_bytes(fig), alt=title)


def build_weekly_graphs(*, labels: Sequence[str], metrics: dict[str, Sequence[Optional[float]]]) -> list[GraphImage]:
    return [
        _build_line_graph(
            cid="weekly-sleep",
            filename="weekly_sleep.png",
            title="Graph 1: Sleep Duration / Sleep Score",
            labels=labels,
            primary=metrics.get("sleep_hours", []),
            primary_label="Sleep hours",
            secondary=metrics.get("sleep_score", []),
            secondary_label="Sleep score",
        ),
        _build_line_graph(
            cid="weekly-mood",
            filename="weekly_mood.png",
            title="Graph 2: Mood Trend",
            labels=labels,
            primary=metrics.get("mood", []),
            primary_label="Mood",
            threshold=2.0,
        ),
        _build_line_graph(
            cid="weekly-expenses",
            filename="weekly_expenses.png",
            title="Graph 3: Daily Expenses",
            labels=labels,
            primary=metrics.get("expenses", []),
            primary_label="JPY",
        ),
        _build_bar_graph(
            cid="weekly-done-drop",
            filename="weekly_done_drop.png",
            title="Graph 4: Done / Drop",
            labels=labels,
            series_a=metrics.get("done", []),
            label_a="Done",
            series_b=metrics.get("drop", []),
            label_b="Drop",
        ),
        _build_line_graph(
            cid="weekly-weight",
            filename="weekly_weight.png",
            title="Graph 5: Weight",
            labels=labels,
            primary=metrics.get("weight", []),
            primary_label="kg",
        ),
    ]
