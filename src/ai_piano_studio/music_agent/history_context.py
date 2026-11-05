"""Bound natural-language history without dropping the latest correction."""
from collections.abc import Mapping, Sequence


def clip_bytes(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    marker = "\n[…历史已缩短…]\n"
    space = max(0, limit - len(marker.encode("utf-8")))
    raw = text.encode("utf-8")
    return (raw[:space // 2].decode("utf-8", errors="ignore") + marker
            + raw[-(space - space // 2):].decode("utf-8", errors="ignore")) if space else ""


def select_history(history, *, budget=6000, max_messages=12):
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return []
    records = [{"role": m["role"], "content": m["content"].strip()}
               for m in history if isinstance(m, Mapping)
               and m.get("role") in {"user", "assistant"}
               and isinstance(m.get("content"), str) and m["content"].strip()]
    if sum(len(m["content"].encode("utf-8")) for m in records) <= budget and len(records) <= max_messages:
        return records
    groups = []
    for message in records:
        if not groups or message["role"] == "user":
            groups.append([])
        groups[-1].append(message)
    if not groups:
        return []
    anchor = None
    if len(groups) > 1 and groups[0][0]["role"] == "user":
        anchor = dict(groups[0][0], content=clip_bytes(groups[0][0]["content"][:800], budget // 4))
    remaining = budget - (len(anchor["content"].encode("utf-8")) if anchor else 0)
    selected = []
    slots = max_messages - bool(anchor)
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if index == 0 and anchor:
            break
        size = sum(len(m["content"].encode("utf-8")) for m in group)
        if selected and (size > remaining or len(group) > slots):
            break  # Keep a contiguous tail, not older replies with their user omitted.
        if not selected and (size > remaining or len(group) > slots):
            group = group[:slots]
            share = remaining // len(group)
            group = [dict(m, content=clip_bytes(m["content"], share)) for m in group]
            size = sum(len(m["content"].encode("utf-8")) for m in group)
        selected = group + selected
        remaining -= size
        slots -= len(group)
    return ([anchor] if anchor else []) + selected
