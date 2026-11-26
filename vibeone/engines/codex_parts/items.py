"""Codex wire-item to VibeOne UI-item conversion helpers."""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class CodexItemsMixin:
    """Converts Codex app-server turn/item payloads into VibeOne chat items."""

    @staticmethod
    def _map_codex_status(status: Any) -> str:
        s = str(status or "").lower()
        if s in ("completed", "done"):
            return "completed"
        if s in ("failed", "declined", "error"):
            return "failed"
        return "in_progress"

    @staticmethod
    def _format_file_change_title(changes: List[Dict[str, Any]]) -> str:
        paths = [Path(str(c.get("path") or "")).name for c in changes if isinstance(c, dict) and c.get("path")]
        if not paths:
            return "修改文件"
        if len(paths) == 1:
            return f"编辑 {paths[0]}"
        return f"编辑 {paths[0]} 等 {len(paths)} 个文件"

    @staticmethod
    def _format_codex_tool_output(item: Dict[str, Any]) -> str:
        err = item.get("error")
        if isinstance(err, dict) and err.get("message"):
            return f"Error: {err['message']}"
        res = item.get("result")
        if isinstance(res, dict):
            content = res.get("content")
            if isinstance(content, list):
                texts = [str(c.get("text")) for c in content if isinstance(c, dict) and c.get("text")]
                if texts:
                    return "\n".join(texts)
            return json.dumps(res, ensure_ascii=False, indent=2)
        if item.get("contentItems"):
            citems = item["contentItems"]
            if isinstance(citems, list):
                texts = [str(c.get("text")) for c in citems if isinstance(c, dict) and c.get("text")]
                if texts:
                    return "\n".join(texts)
            return json.dumps(citems, ensure_ascii=False, indent=2)
        return ""

    @classmethod
    def _convert_codex_item_to_ui(cls, item: Dict[str, Any], ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        itype = item.get("type")
        iid = str(item.get("id") or uuid.uuid4().hex[:12])
        now = float(ts) if ts is not None else time.time()

        if itype == "userMessage":
            texts: List[str] = []
            for blk in item.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    t = str(blk.get("text") or "").strip()
                    if t:
                        texts.append(t)
            return {
                "id": f"user_{iid}",
                "type": "user",
                "text": "\n".join(texts),
                "images": [],
                "ts": now,
            }

        if itype == "agentMessage":
            return {
                "id": f"asst_{iid}",
                "type": "assistant",
                "text": str(item.get("text") or ""),
                "ts": now,
            }

        if itype == "reasoning":
            summary = [str(x) for x in (item.get("summary") or []) if x]
            content = [str(x) for x in (item.get("content") or []) if x]
            text = "\n".join(summary or content)
            return {
                "id": f"thought_{iid}",
                "type": "thought",
                "text": text,
                "ts": now,
            }

        if itype == "plan":
            return {
                "id": f"thought_{iid}",
                "type": "thought",
                "text": str(item.get("text") or ""),
                "ts": now,
            }

        if itype == "commandExecution":
            cmd = str(item.get("command") or "Shell")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "commandExecution",
                "title": cmd,
                "kind": "execute",
                "status": cls._map_codex_status(item.get("status")),
                "args": {"command": cmd, "cwd": str(item.get("cwd") or "")},
                "output": str(item.get("aggregatedOutput") or ""),
                "ts": now,
            }

        if itype == "fileChange":
            changes = item.get("changes") or []
            diffs = []
            for c in changes:
                if isinstance(c, dict) and c.get("diff"):
                    diffs.append(f"--- {c.get('path', '')}\n{c.get('diff', '')}")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "fileChange",
                "title": cls._format_file_change_title(changes),
                "kind": "edit",
                "status": cls._map_codex_status(item.get("status")),
                "args": {
                    "changes": [
                        {"path": c.get("path"), "kind": c.get("kind")}
                        for c in changes
                        if isinstance(c, dict)
                    ]
                },
                "output": "\n\n".join(diffs),
                "ts": now,
            }

        if itype in ("mcpToolCall", "dynamicToolCall"):
            server_name = str(item.get("server") or "").strip()
            tool_name = str(item.get("tool") or itype).strip()
            title = f"{server_name}:{tool_name}" if server_name else tool_name
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": tool_name,
                "title": title,
                "kind": "other",
                "status": cls._map_codex_status(item.get("status")),
                "args": item.get("arguments") or {},
                "output": cls._format_codex_tool_output(item),
                "ts": now,
            }

        if itype == "webSearch":
            q = str(item.get("query") or "Web Search")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "webSearch",
                "title": f"搜索: {q}",
                "kind": "search",
                "status": "completed",
                "args": {"query": q},
                "output": "",
                "ts": now,
            }

        if itype == "imageView":
            p = str(item.get("path") or "")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "imageView",
                "title": f"查看图片 {Path(p).name if p else ''}".strip(),
                "kind": "read",
                "status": "completed",
                "args": {"path": p},
                "output": "",
                "ts": now,
            }

        if itype == "collabAgentToolCall":
            tname = str(item.get("tool") or "subagent")
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": tname,
                "title": tname,
                "kind": "other",
                "status": cls._map_codex_status(item.get("status")),
                "args": {"prompt": str(item.get("prompt") or "")},
                "output": "",
                "ts": now,
            }

        if itype == "contextCompaction":
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "contextCompaction",
                "title": "压缩上下文历史",
                "kind": "other",
                "status": "completed",
                "args": {},
                "output": "",
                "ts": now,
            }

        if itype == "enteredReviewMode":
            return {
                "id": f"tool_{iid}",
                "type": "tool",
                "toolCallId": iid,
                "name": "review",
                "title": "开始代码审查",
                "kind": "other",
                "status": "in_progress",
                "args": {},
                "output": str(item.get("review") or ""),
                "ts": now,
            }

        if itype == "exitedReviewMode":
            review_text = str(item.get("review") or "").strip()
            if review_text:
                return {
                    "id": f"asst_{iid}",
                    "type": "assistant",
                    "text": review_text,
                    "ts": now,
                }
            return None

        return None

    @classmethod
    def _convert_codex_turns_to_items(cls, turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            ts = float(turn.get("startedAt") or time.time())
            for raw_item in turn.get("items") or []:
                ui_item = cls._convert_codex_item_to_ui(raw_item, ts=ts)
                if ui_item and (ui_item["type"] != "assistant" or ui_item.get("text")):
                    if ui_item["type"] == "thought" and not ui_item.get("text"):
                        continue
                    out.append(ui_item)
        return out
