"""Safe, actionable provider feedback for registered Music Tool results."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ai_piano_studio.music_agent.tool_contracts import MusicToolResult


MAX_PROVIDER_FEEDBACK_BYTES = 16_384
MAX_RESOURCE_FEEDBACK_BYTES = 48_000
MAX_PROVIDER_FEEDBACK_SUMMARY_CHARS = 240
MAX_PROVIDER_FEEDBACK_WARNINGS = 4
MAX_PROVIDER_FEEDBACK_WARNING_CHARS = 120
MAX_PROVIDER_FEEDBACK_STRING_CHARS = 256


def serialize_provider_feedback(
    result: MusicToolResult,
    *,
    max_bytes: int | None = None,
) -> str:
    """Serialize one result through a tool-specific whitelist and byte budget."""
    if max_bytes is None:
        max_bytes = (MAX_RESOURCE_FEEDBACK_BYTES if result.tool_name == 'inspect_music_resource'
                     else MAX_PROVIDER_FEEDBACK_BYTES)

    if max_bytes < 512:
        raise ValueError("max_bytes must leave room for a safe tool result")
    data = _safe_feedback_data(result.tool_name, result.data)
    while True:
        payload = _payload(result, data)
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return encoded
        if not _reduce_actionable_data(result.tool_name, data):
            break
    fallback = {
        "success": result.success,
        "tool": result.tool_name,
        "summary": "tool result is available",
        "data": _minimal_feedback_data(result.tool_name, data),
        "error_code": result.error_code,
    }
    encoded = json.dumps(fallback, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= max_bytes:
        return encoded
    return json.dumps(
        {
            "success": result.success,
            "tool": result.tool_name,
            "data": {"truncated": True},
            "error_code": result.error_code,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _payload(result: MusicToolResult, data: Mapping[str, object]) -> dict[str, object]:
    return {
        "success": result.success,
        "tool": result.tool_name,
        "summary": result.summary[:MAX_PROVIDER_FEEDBACK_SUMMARY_CHARS],
        "data": data,
        "added_notes": result.added_notes,
        "updated_notes": result.updated_notes,
        "deleted_notes": result.deleted_notes,
        "warnings": [
            warning[:MAX_PROVIDER_FEEDBACK_WARNING_CHARS]
            for warning in result.warnings[:MAX_PROVIDER_FEEDBACK_WARNINGS]
        ],
        "error_code": result.error_code,
    }


def _safe_feedback_data(tool_name: str, source: Mapping[str, object]) -> dict[str, object]:
    if tool_name == 'analyze_audition':
        keys = ('start_bar', 'end_bar', 'start_seconds', 'end_seconds', 'note_count',
                'rendered_seconds', 'sample_rate', 'channels', 'peak_dbfs', 'rms_dbfs',
                'clipped_sample_count', 'max_polyphony', 'notes_per_second', 'min_pitch', 'max_pitch')
        data = {key: source[key] for key in keys if key in source and (_is_number(source[key]) or source[key] is None)}
        data.update({key: source[key][:400] for key in ('status', 'analysis_kind', 'source', 'warning', 'reason', 'next_action')
                     if isinstance(source.get(key), str)})
        if type(source.get('audible')) is bool:
            data['audible'] = source['audible']
        return data
    if tool_name in {'set_song_task', 'start_original_draft', 'start_song_draft'}:
        data = {key: source[key][:800] for key in
                ('status', 'task_id', 'title', 'artist', 'section', 'next_action')
                if isinstance(source.get(key), str)}
        for key in ('duration_seconds', 'start_seconds', 'version'):
            if _is_number(source.get(key)):
                data[key] = source[key]
        return data
    if tool_name in {'get_ui_style','apply_ui_style','restore_ui_style'}:
        from ai_piano_studio.music_agent.style_tools import STYLE_PROPERTIES
        data={k:source[k][:200] for k in ('status','preset_id','reason') if isinstance(source.get(k),str)}
        values=source.get('values',{})
        if isinstance(values,Mapping):
            data['values']={k:v for k,v in values.items() if k in STYLE_PROPERTIES
                            and isinstance(v,(str,bool,int,float)) and len(str(v))<=100}
        if tool_name=='get_ui_style':
            data['supported']=STYLE_PROPERTIES
            data['presets']=[dict(id=p['id'][:64],name=p['name'][:80]) for p in source.get('presets',())[:100]
                             if isinstance(p,Mapping) and isinstance(p.get('id'),str) and isinstance(p.get('name'),str)]
            data['can_restore_previous']=bool(source.get('can_restore_previous'))
        return data
    if tool_name in {'inspect_music_resource', 'read_resource_notes', 'prepare_song_preview', 'propose_song_source'}:
        data = {}
        for key in ('status', 'resource_id', 'title', 'url', 'source_url', 'source_sha256', 'warning', 'reason', 'next_action', 'untrusted_text'):
            value = source.get(key)
            if isinstance(value, str):
                if key == 'untrusted_text':
                    from ai_piano_studio.music_agent.resource_tools import RESOURCE_TEXT_PAGE_CHARS
                    data[key] = value[:RESOURCE_TEXT_PAGE_CHARS]
                else:
                    data[key] = value[:2048 if key.endswith('url') else 256]
        dimensions = source.get('source_dimensions')
        if (isinstance(dimensions, (list, tuple)) and len(dimensions) == 2
                and all(type(value) is int and value > 0 for value in dimensions)
                and dimensions[0] * dimensions[1] <= 40_000_000):
            data['source_dimensions'] = list(dimensions)
        if source.get('images_delivered') is True:
            data['images_delivered'] = True
        for key in ('offset', 'next_offset', 'text_offset', 'next_text_offset', 'link_offset',
                    'next_link_offset', 'total_text_chars', 'total_links', 'note_count', 'duration_seconds', 'page_count'):
            value = source.get(key)
            if value is None and key.startswith('next_') or _is_number(value):
                data[key] = value
        if isinstance(source.get('rendered_pages'), list):
            data['rendered_pages'] = [n for n in source['rendered_pages'][:2] if isinstance(n, int) and not isinstance(n, bool)]
        if isinstance(source.get('image_regions'), list):
            data['image_regions'] = [dict(page=row['page'], region=row['region'],
                **({'crop': list(row['crop'])} if row.get('region') == 'crop'
                   and isinstance(row.get('crop'), (list, tuple)) and len(row['crop']) == 4
                   and all(_is_number(v) and 0 <= v <= 1 for v in row['crop']) else {}))
                for row in source['image_regions'][:2]
                if isinstance(row, Mapping) and type(row.get('page')) is int and row['page'] >= 1
                and row.get('region') in {'overview', 'upper_systems', 'crop'}]
        for key in ('links', 'download_links', 'reference_links', 'image_links', 'tracks'):
            if isinstance(source.get(key), list):
                data[key] = [v[:2048] for v in source[key][:40] if isinstance(v, str)]
        if isinstance(source.get('notes'), list):
            data['notes'] = [{k:n[k] for k in ('pitch','start_time','duration','velocity','track') if _is_number(n.get(k))}
                             for n in source['notes'][:32] if isinstance(n, Mapping)]
        return data
    if tool_name == "get_task_history":
        return {"instructions": list(source.get("instructions", ()))[:3],
                "nextOffset": source.get("nextOffset", 0), "hasMore": bool(source.get("hasMore")),
                "taskId": source.get("taskId", ""), "version": source.get("version", 0)}
    if tool_name == "get_notes":
        return _safe_note_feedback(source)
    if tool_name == "get_harmony_context":
        return _safe_harmony_feedback(source)
    if "invalid_items" in source or "valid_item_count" in source:
        return _safe_validation_feedback(source)
    compact: dict[str, object] = {}
    for key in (
        "bpm",
        "beats_per_bar",
        "beat_unit",
        "active_note_count",
        "start_bar",
        "end_bar",
    ):
        value = source.get(key)
        if _is_number(value):
            compact[key] = value
    return compact


def _safe_validation_feedback(source: Mapping[str, object]) -> dict[str, object]:
    """Keep only bounded, actionable validation details for the provider."""

    result: dict[str, object] = {}
    raw_items = source.get("invalid_items")
    if isinstance(raw_items, list):
        items: list[dict[str, object]] = []
        for raw_item in raw_items[:128]:
            if not isinstance(raw_item, Mapping):
                continue
            item: dict[str, object] = {}
            item_index = raw_item.get("item_index")
            if isinstance(item_index, int) and not isinstance(item_index, bool) and item_index >= 0:
                item["item_index"] = item_index
            for key in ("field", "reason"):
                value = raw_item.get(key)
                if isinstance(value, str) and value:
                    item[key] = value[:64]
            for key in ("start", "duration", "authorized_start", "authorized_end"):
                value = raw_item.get(key)
                if _is_number(value):
                    item[key] = value
            if item:
                items.append(item)
        result["invalid_items"] = items
    for key in ("valid_item_count", "total_item_count"):
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    preserve = source.get("preserve_valid_items")
    if isinstance(preserve, bool):
        result["preserve_valid_items"] = preserve
    return result


def _safe_note_feedback(source: Mapping[str, object]) -> dict[str, object]:
    raw_notes = source.get("notes")
    raw_items = raw_notes if isinstance(raw_notes, list) else []
    notes = [record for item in raw_items if (record := _safe_note(item)) is not None]
    result = _safe_range(source)
    offset = source.get('offset', 0)
    total = source.get('total_note_count', len(raw_items))
    if type(offset) is not int or offset < 0:
        offset = 0
    if type(total) is not int or total < len(raw_items):
        total = len(raw_items)
    if source.get("range_semantics") == "overlap":
        result["range_semantics"] = "overlap"
    result.update(
        {
            "notes": notes,
            "returned_note_count": len(notes),
            "total_note_count": total,
            "offset": offset,
            "next_offset": offset + len(notes) if offset + len(notes) < total else None,
            "truncated": len(notes) != len(raw_items) or offset + len(notes) < total,
        }
    )
    return result


def _safe_note(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    note_id = value.get("id")
    if not isinstance(note_id, str) or not note_id:
        return None
    result: dict[str, object] = {"id": note_id[:MAX_PROVIDER_FEEDBACK_STRING_CHARS]}
    for key in ("pitch", "start_time", "duration", "velocity"):
        item = value.get(key)
        if _is_number(item):
            result[key] = item
    return result


def _safe_harmony_feedback(source: Mapping[str, object]) -> dict[str, object]:
    raw_measures = source.get("measures")
    raw_items = raw_measures if isinstance(raw_measures, list) else []
    measures = [record for item in raw_items if (record := _safe_measure(item)) is not None]
    total_candidates = sum(
        len(item.get("candidates"))
        for item in raw_items
        if isinstance(item, Mapping) and isinstance(item.get("candidates"), list)
    )
    returned_candidates = sum(len(item.get("candidates", [])) for item in measures)
    result = _safe_range(source)
    token = source.get("harmony_context_token")
    if isinstance(token, str) and token:
        result["harmony_context_token"] = token[:MAX_PROVIDER_FEEDBACK_STRING_CHARS]
    result.update(
        {
            "measures": measures,
            "returned_measure_count": len(measures),
            "total_measure_count": len(raw_items),
            "returned_candidate_count": returned_candidates,
            "total_candidate_count": total_candidates,
            "truncated": len(measures) != len(raw_items)
            or returned_candidates != total_candidates,
        }
    )
    return result


def _safe_measure(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    measure_index = value.get("measure_index")
    if not isinstance(measure_index, int) or isinstance(measure_index, bool):
        return None
    raw_candidates = value.get("candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    safe_candidates = [
        record for item in candidates if (record := _safe_candidate(item)) is not None
    ]
    return {"measure_index": measure_index, "candidates": safe_candidates}


def _safe_candidate(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    candidate_index = value.get("candidate_index")
    if not isinstance(candidate_index, int) or isinstance(candidate_index, bool):
        return None
    result: dict[str, object] = {"candidate_index": candidate_index}
    for key in ("chord_symbol", "quality"):
        item = value.get(key)
        if isinstance(item, str):
            result[key] = item[:MAX_PROVIDER_FEEDBACK_STRING_CHARS]
    score = value.get("score")
    if _is_number(score):
        result["score"] = score
    return result


def _safe_range(source: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in ("start_bar", "end_bar"):
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
    return result


def _reduce_actionable_data(tool_name: str, data: dict[str, object]) -> bool:
    if tool_name in {'get_ui_style','apply_ui_style','restore_ui_style'}:
        if data.get('presets'):
            data['presets'].pop()
            data['truncated']=True
            return True
        if data.get('supported'):
            data['supported']={}
            data['truncated']=True
            return True
        return False
    if tool_name in {'inspect_music_resource', 'read_resource_notes', 'prepare_song_preview', 'propose_song_source'}:
        text = data.get('untrusted_text', '')
        if len(text) > 500:
            data['untrusted_text'] = text[:len(text)//2]
            if 'text_offset' in data:
                data['next_text_offset'] = data['text_offset'] + len(data['untrusted_text'])
            data['truncated'] = True
            return True
        for key in ('links', 'download_links', 'reference_links', 'image_links', 'tracks', 'notes'):
            if isinstance(data.get(key), list) and data[key]:
                # Preserve the last actionable candidate, shrinking duplicate
                # prose/links before the only usable download URL.
                if key == 'download_links' and len(data[key]) == 1:
                    continue
                if key == 'links' and len(data[key]) == 1 and not data.get('download_links'):
                    continue
                data[key].pop()
                data['truncated'] = True
                if key == 'notes':
                    data['next_offset'] = data.get('offset', 0) + len(data['notes'])
                if key == 'links' and 'link_offset' in data:
                    data['next_link_offset'] = data['link_offset'] + len(data['links'])
                return True
        text = data.get('untrusted_text', '')
        if len(text) > 100:
            data['untrusted_text'] = text[:len(text)//2]
            if 'text_offset' in data:
                data['next_text_offset'] = data['text_offset'] + len(data['untrusted_text'])
            data['truncated'] = True
            return True
        return False
    if tool_name == "get_notes":
        notes = data.get("notes")
        if isinstance(notes, list) and notes:
            remove_count = max(1, len(notes) // 4)
            del notes[-remove_count:]
            data["returned_note_count"] = len(notes)
            data['next_offset'] = data.get('offset', 0) + len(notes)
            data["truncated"] = True
            return True
        return False
    if tool_name == "get_harmony_context":
        measures = data.get("measures")
        if not isinstance(measures, list) or not measures:
            return False
        candidate_owner = next(
            (
                measure
                for measure in reversed(measures)
                if isinstance(measure, dict)
                and isinstance(measure.get("candidates"), list)
                and len(measure["candidates"]) > 1
            ),
            None,
        )
        if candidate_owner is not None:
            candidate_owner["candidates"].pop()
        elif len(measures) > 1:
            measures.pop()
        else:
            only_candidates = measures[0].get("candidates")
            if not isinstance(only_candidates, list) or not only_candidates:
                return False
            only_candidates.pop()
        data["returned_measure_count"] = len(measures)
        data["returned_candidate_count"] = sum(
            len(measure.get("candidates", []))
            for measure in measures
            if isinstance(measure, Mapping)
        )
        data["truncated"] = True
        return True
    return False


def _minimal_feedback_data(tool_name: str, data: Mapping[str, object]) -> dict[str, object]:
    if tool_name in {'get_ui_style','apply_ui_style','restore_ui_style'}:
        return {k:data[k] for k in ('status','preset_id') if k in data}
    if tool_name in {'inspect_music_resource', 'read_resource_notes', 'prepare_song_preview', 'propose_song_source'}:
        return {**{k:data[k] for k in ('status','resource_id','offset','next_offset') if k in data}, 'truncated':True}
    if tool_name == "get_notes":
        return {
            **{key: data[key] for key in ("start_bar", "end_bar", "range_semantics") if key in data},
            "notes": [],
            "returned_note_count": 0,
            "total_note_count": data.get("total_note_count", 0),
            "offset": data.get('offset', 0),
            "next_offset": data.get('offset', 0),
            "truncated": True,
        }
    if tool_name == "get_harmony_context":
        return {
            **{
                key: data[key]
                for key in ("start_bar", "end_bar", "harmony_context_token")
                if key in data
            },
            "measures": [],
            "returned_measure_count": 0,
            "total_measure_count": data.get("total_measure_count", 0),
            "returned_candidate_count": 0,
            "total_candidate_count": data.get("total_candidate_count", 0),
            "truncated": True,
        }
    return {}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
