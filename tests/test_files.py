"""文件工具测试：重点是附件的落盘与路径安全。"""

import io
from pathlib import Path

from quill_agent.tools import files


class FakeUpload(io.BytesIO):
    """Streamlit 的 UploadedFile 就是 BytesIO 子类，多一个 name 属性。"""

    def __init__(self, name: str, data: bytes) -> None:
        super().__init__(data)
        self.name = name


def test_saves_attachment_into_work_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)

    results = files.save_attachments([FakeUpload("a.txt", b"hello")])

    assert [item.describe() for item in results] == [f"{files.ATTACHMENTS_DIR}/a.txt"]
    assert (tmp_path / files.ATTACHMENTS_DIR / "a.txt").read_bytes() == b"hello"


def test_attachment_name_cannot_escape_work_dir(monkeypatch, tmp_path: Path) -> None:
    """文件名来自浏览器，不能当成可信路径 —— 只取末级名。"""
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)

    results = files.save_attachments([FakeUpload("../../evil.txt", b"x")])

    assert [item.describe() for item in results] == [f"{files.ATTACHMENTS_DIR}/evil.txt"]
    assert not (tmp_path.parent / "evil.txt").exists()


def test_same_name_overwrites(monkeypatch, tmp_path: Path) -> None:
    """附件是这一轮的输入，不需要保留历史版本。"""
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)

    files.save_attachments([FakeUpload("a.txt", b"first")])
    files.save_attachments([FakeUpload("a.txt", b"second")])

    assert (tmp_path / files.ATTACHMENTS_DIR / "a.txt").read_bytes() == b"second"


def test_no_files_returns_empty(monkeypatch, tmp_path: Path) -> None:
    """没有附件时连目录都不该建。"""
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)

    assert files.save_attachments([]) == []
    assert not (tmp_path / files.ATTACHMENTS_DIR).exists()


def test_multiple_attachments(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)

    results = files.save_attachments(
        [FakeUpload("a.txt", b"1"), FakeUpload("b.md", b"2")],
    )

    assert [item.describe() for item in results] == [
        f"{files.ATTACHMENTS_DIR}/a.txt",
        f"{files.ATTACHMENTS_DIR}/b.md",
    ]


# ---------------------------------------------------------------------------
# 路径解析与校验（_target）
#
# 直接测这个内部函数：它是七个个工具共同的第一步，也是整套文件工具的**安全
# 边界**。单独测它比逐个工具去构造「越界路径」省事得多，覆盖面还更全。
# ---------------------------------------------------------------------------


def _in_tmp_work_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)


def test_target_rejects_path_outside_work_dir(monkeypatch, tmp_path: Path) -> None:
    """越界路径必须被拦下 —— 这是「模型不能碰工作目录外面」的唯一保证。"""
    _in_tmp_work_dir(monkeypatch, tmp_path)

    target, error = files._target("../../etc/passwd")

    assert target is None
    assert error  # 具体文案由 PathGuard 给，这里只要求给出理由


def test_target_reports_missing_path(monkeypatch, tmp_path: Path) -> None:
    _in_tmp_work_dir(monkeypatch, tmp_path)

    target, error = files._target("nope.txt")

    assert target is None
    assert error == "路径不存在：nope.txt"


def test_target_checks_expected_kind(monkeypatch, tmp_path: Path) -> None:
    """要文件却给了目录（反之亦然）时，错误文案要说清是哪种不对。"""
    _in_tmp_work_dir(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    assert "不是文件" in files._target("sub", want="file")[1]
    assert "不是目录" in files._target("a.txt", want="dir")[1]


def test_target_allows_missing_when_creating(monkeypatch, tmp_path: Path) -> None:
    """write_file 的职责就是新建文件，所以它必须能接受还不存在的路径。"""
    _in_tmp_work_dir(monkeypatch, tmp_path)

    target, error = files._target("new.txt", want="file", must_exist=False)

    assert error == ""
    assert target == tmp_path / "new.txt"


def test_target_clamps_inside_work_dir(monkeypatch, tmp_path: Path) -> None:
    """合法路径能正常解析，且结果确实落在工作目录里。"""
    _in_tmp_work_dir(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    target, error = files._target("a.txt", want="file")

    assert error == ""
    assert target == tmp_path / "a.txt"
