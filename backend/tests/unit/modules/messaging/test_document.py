"""Rich message content boundaries and attachment membership."""

from uuid import UUID

import pytest

from klack.modules.messaging.domain.document import validate_document
from klack.modules.messaging.domain.errors import InvalidMessageBody


def paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_attachment_positions_and_fallback_text() -> None:
    attachment = UUID(int=5)
    document = {
        "type": "doc",
        "content": [
            paragraph("Before"),
            {"type": "attachment", "attrs": {"id": str(attachment)}},
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print(1)"}],
            },
            paragraph("After"),
        ],
    }
    validate_document(document, "Before\n\nprint(1)\nAfter", (attachment,))
    for attachments in [(), (UUID(int=6),)]:
        with pytest.raises(InvalidMessageBody):
            validate_document(document, "Before\n\nprint(1)\nAfter", attachments)
    with pytest.raises(InvalidMessageBody):
        validate_document(document, "different fallback", (attachment,))
    document["content"].append({"type": "attachment", "attrs": {"id": str(attachment)}})
    with pytest.raises(InvalidMessageBody):
        validate_document(document, "Before\n\nprint(1)\nAfter", (attachment,))


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "data:text/html,hello",
        "https://[broken",
        "https://good.test\n",
        "file:///etc/passwd",
    ],
)
def test_rejects_unsafe_links(href: str) -> None:
    document = {"type": "doc", "content": [paragraph("link")]}
    document["content"][0]["content"][0]["marks"] = [{"type": "link", "attrs": {"href": href}}]
    with pytest.raises(InvalidMessageBody):
        validate_document(document, "link", ())


@pytest.mark.parametrize(
    "node",
    [
        {"type": "script", "text": "alert(1)"},
        {"type": []},
        {"type": "paragraph", "attrs": {"onclick": "alert(1)"}},
        {"type": "attachment", "attrs": {"id": "bad"}},
        {"type": "paragraph", "content": "bad"},
    ],
)
def test_rejects_unknown_and_malformed_nodes(node: dict) -> None:
    with pytest.raises(InvalidMessageBody):
        validate_document({"type": "doc", "content": [node]}, "", ())


def test_bounds_node_count_and_depth() -> None:
    with pytest.raises(InvalidMessageBody):
        validate_document({"type": "doc", "content": [paragraph("x")] * 501}, "", ())
    node = paragraph("x")
    for _ in range(13):
        node = {"type": "blockquote", "content": [node]}
    with pytest.raises(InvalidMessageBody):
        validate_document({"type": "doc", "content": [node]}, "x", ())
