"""Bounded, allowlisted rich messages with authorized attachment references."""

import json
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from klack.modules.messaging.domain.errors import InvalidMessageBody

Document = dict[str, Any]


def validate_document(document: Document | None, body: str, attachments: tuple[UUID, ...]) -> None:
    """Check structure, fallback text, and exact attachment membership before persistence."""
    if document is None:
        return
    try:
        size = len(json.dumps(document, ensure_ascii=False))
    except (RecursionError, TypeError, ValueError) as error:
        raise InvalidMessageBody from error
    if size > 64_000 or document.get("type") != "doc":
        raise InvalidMessageBody
    references: list[UUID] = []
    count = 0
    children = {
        "doc": {"paragraph", "blockquote", "bulletList", "orderedList", "codeBlock", "attachment"},
        "paragraph": {"text", "hardBreak"},
        "blockquote": {
            "paragraph",
            "blockquote",
            "bulletList",
            "orderedList",
            "codeBlock",
            "attachment",
        },
        "bulletList": {"listItem"},
        "orderedList": {"listItem"},
        "listItem": {
            "paragraph",
            "bulletList",
            "orderedList",
            "blockquote",
            "codeBlock",
            "attachment",
        },
        "codeBlock": {"text"},
        "text": set(),
        "hardBreak": set(),
        "attachment": set(),
    }

    def visit(node: Any, depth: int) -> str:
        nonlocal count
        count += 1
        if not isinstance(node, dict) or depth > 12 or count > 500:
            raise InvalidMessageBody
        kind = node.get("type")
        if (
            not isinstance(kind, str)
            or kind not in children
            or set(node) - {"type", "content", "attrs", "text", "marks"}
        ):
            raise InvalidMessageBody
        attrs = node.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise InvalidMessageBody
        allowed_attrs = {
            "attachment": {"id"},
            "codeBlock": {"language"},
            "orderedList": {"start", "type"},
        }
        if set(attrs) - allowed_attrs.get(kind, set()):
            raise InvalidMessageBody
        if kind == "attachment":
            try:
                references.append(UUID(attrs["id"]))
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                raise InvalidMessageBody from error
        if kind == "codeBlock" and attrs.get("language") is not None:
            language = attrs["language"]
            if (
                not isinstance(language, str)
                or len(language) > 40
                or not all(char.isalnum() or char in "_+-" for char in language)
            ):
                raise InvalidMessageBody
        if kind == "orderedList" and (
            type(attrs.get("start", 1)) is not int or not 1 <= attrs.get("start", 1) <= 10000
        ):
            raise InvalidMessageBody
        if kind == "orderedList" and attrs.get("type") not in (None, "1", "a", "A", "i", "I"):
            raise InvalidMessageBody
        marks = node.get("marks", [])
        if not isinstance(marks, list) or len(marks) > 5 or (marks and kind != "text"):
            raise InvalidMessageBody
        for mark in marks:
            if not isinstance(mark, dict) or set(mark) - {"type", "attrs"}:
                raise InvalidMessageBody
            if not isinstance(mark.get("type"), str) or mark.get("type") not in {
                "bold",
                "italic",
                "strike",
                "code",
                "link",
            }:
                raise InvalidMessageBody
            mark_attrs = mark.get("attrs") or {}
            if not isinstance(mark_attrs, dict):
                raise InvalidMessageBody
            if mark["type"] == "link":
                href = mark_attrs.get("href")
                if set(mark_attrs) - {"href", "target", "rel", "class"} or not isinstance(
                    href, str
                ):
                    raise InvalidMessageBody
                try:
                    scheme = urlsplit(href).scheme.lower()
                except ValueError as error:
                    raise InvalidMessageBody from error
                if len(href) > 2048 or scheme not in {
                    "https",
                    "http",
                    "mailto",
                }:
                    raise InvalidMessageBody
                if any(ord(char) < 32 for char in href):
                    raise InvalidMessageBody
            elif mark_attrs:
                raise InvalidMessageBody
        content = node.get("content", [])
        if not isinstance(content, list):
            raise InvalidMessageBody
        for child in content:
            if (
                not isinstance(child, dict)
                or not isinstance(child.get("type"), str)
                or child.get("type") not in children[kind]
            ):
                raise InvalidMessageBody
        if kind == "text":
            if not isinstance(node.get("text"), str) or not node["text"] or content:
                raise InvalidMessageBody
            return str(node["text"])
        if "text" in node:
            raise InvalidMessageBody
        if kind == "hardBreak":
            return "\n"
        separator = "" if kind in {"paragraph", "codeBlock"} else "\n"
        return separator.join(visit(child, depth + 1) for child in content)

    if visit(document, 0).strip() != body.strip():
        raise InvalidMessageBody
    if len(references) != len(set(references)) or set(references) != set(attachments):
        raise InvalidMessageBody
