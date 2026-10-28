"""Two prints of the same CV at once (a preview asked for twice) must not break each other: each prints to its own
file and swaps it in whole; a failed print leaves the last good PDF and no stray files."""
import io
import threading
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.render import Renderer


def _pdf_bytes(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class FakePage:
    def __init__(self, pages=1, fail=False):
        self.pages, self.fail, self.loaded = pages, fail, []

    def goto(self, uri, wait_until=None):
        assert Path(uri.replace("file:///", "")).exists()   # its own page, never deleted by another print
        self.loaded.append(uri)

    def evaluate(self, js):
        return True

    def pdf(self, path, **kw):
        if self.fail:
            raise RuntimeError("chrome crashed")
        Path(path).write_bytes(_pdf_bytes(self.pages))


def _renderer(page):
    r = Renderer.__new__(Renderer)
    r._page = page
    return r


def test_concurrent_prints_each_use_their_own_files(tmp_path):
    tmp_path = tmp_path / "prints"
    tmp_path.mkdir()
    out = tmp_path / "preview.pdf"
    pages, errors = [], []

    def go(n):
        try:
            pages.append(_renderer(FakePage(n)).pdf("<p>cv</p>", out))
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=go, args=(n,)) for n in (1, 2, 3, 1, 2, 3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and sorted(pages) == [1, 1, 2, 2, 3, 3]
    assert [p.name for p in tmp_path.iterdir()] == ["preview.pdf"]   # no half-written or leftover files


def test_a_failed_print_keeps_the_last_good_pdf(tmp_path):
    tmp_path = tmp_path / "prints"
    tmp_path.mkdir()
    out = tmp_path / "cv.pdf"
    _renderer(FakePage(2)).pdf("<p>cv</p>", out)
    good = out.read_bytes()
    with pytest.raises(RuntimeError):
        _renderer(FakePage(fail=True)).pdf("<p>cv</p>", out)
    assert out.read_bytes() == good and [p.name for p in tmp_path.iterdir()] == ["cv.pdf"]
