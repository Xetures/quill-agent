"""附件：图片以「内容块」交给模型，文本类仍然走 read_file。

图片没法变成文本，而工具的结果契约是「返回一段文本」，所以它只能在组装消息时
作为内容块放进去。这一批用例盯的就是「什么时候会变成内容块、什么时候不」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quill_agent.agent import build_user_message, image_content
from quill_agent.tools import files


class FakeUpload:
    """上传文件的鸭子类型替身：有 name、能 read() 就够。"""

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture(autouse=True)
def _work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)
    return tmp_path


def _save(*items: FakeUpload) -> list[files.SavedAttachment]:
    return files.save_attachments(list(items))


# ---------------------------------------------------------------------------
# 落盘结果：结构化，不再是一行要解析的字符串
# ---------------------------------------------------------------------------


def test_saved_attachment_carries_the_path() -> None:
    (item,) = _save(FakeUpload("a.txt", b"hello"))

    assert item.path is not None
    assert item.relative == f"{files.ATTACHMENTS_DIR}/a.txt"
    assert item.describe() == f"{files.ATTACHMENTS_DIR}/a.txt"


def test_failed_save_is_reported_in_describe() -> None:
    """失败行和成功行必须分得出来 —— 原先调用方只能去猜字符串的形状。"""
    item = files.SavedAttachment(name="a.txt", error="磁盘满了")

    assert item.path is None
    assert "保存失败" in item.describe()


# ---------------------------------------------------------------------------
# 图片识别
# ---------------------------------------------------------------------------


def test_image_is_marked_and_becomes_a_data_url() -> None:
    (item,) = _save(FakeUpload("shot.png", b"\x89PNG\r\n\x1a\n"))

    assert item.is_image
    url = files.image_data_url(item.path)
    assert url is not None
    assert url.startswith("data:image/png;base64,")


def test_jpeg_gets_the_right_mime() -> None:
    """`jpg` 的 mime 是 image/jpeg —— 直接拼成 image/jpg 有些服务会不认。"""
    (item,) = _save(FakeUpload("photo.JPG", b"\xff\xd8\xff"))

    assert files.image_data_url(item.path).startswith("data:image/jpeg;base64,")


def test_text_file_is_not_an_image() -> None:
    (item,) = _save(FakeUpload("a.txt", b"hi"))

    assert not item.is_image
    assert files.image_data_url(item.path) is None


def test_bmp_is_not_treated_as_an_image() -> None:
    """两家接口都不收 bmp，当图片发出去只会换来一个 400。"""
    (item,) = _save(FakeUpload("old.bmp", b"BM"))

    assert not item.is_image


def test_oversize_image_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "MAX_IMAGE_BYTES", 4)
    (item,) = _save(FakeUpload("big.png", b"12345678"))

    assert item.is_image  # 类型上还是图片
    assert files.image_data_url(item.path) is None  # 但不会发出去


# ---------------------------------------------------------------------------
# 拼消息
# ---------------------------------------------------------------------------


def test_text_only_message_stays_a_plain_string() -> None:
    """没有图片时请求体和以前一字不差 —— 别让所有调用方为偶尔才用的能力买单。"""
    assert image_content("你好", []) == "你好"


def test_image_attachments_become_content_blocks() -> None:
    attachments = _save(FakeUpload("shot.png", b"\x89PNG"), FakeUpload("note.txt", b"hi"))

    content = image_content("看看这张图", attachments)

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看看这张图"}
    assert len(content) == 2  # 只有图片那一个变成了块
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_failed_attachment_does_not_break_the_message() -> None:
    attachments = [files.SavedAttachment(name="a.png", error="写不进去")]

    assert image_content("你好", attachments) == "你好"


def test_user_message_lists_attachments_and_keeps_them_apart() -> None:
    """正文里要说清两条路：图片已经给它了，文本附件才要它自己去读。"""
    text = build_user_message("看看", _save(FakeUpload("shot.png", b"\x89PNG")))

    assert f"{files.ATTACHMENTS_DIR}/shot.png" in text
    assert "read_file" in text


def test_user_message_without_attachments_has_no_attachment_section() -> None:
    text = build_user_message("你好", [])

    assert "附件" not in text
    assert text.endswith("你好")
