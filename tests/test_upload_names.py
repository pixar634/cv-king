"""A CV or cover letter attached to a form goes as Name_CV.pdf: some sites refuse a long file name, with a pop-up
that stops the form."""
import json

from fastapi.testclient import TestClient

from app import tailor
from app.config import output_dir
from app.main import app

LOCAL = TestClient(app, base_url="http://127.0.0.1:8765")


def test_upload_name_is_short_and_says_whose_it_is():
    profile = {"basics": {"name": "Test Q. Person-Longname"}}
    assert tailor.upload_name(profile) == "Test_Q_Person_Longname_CV.pdf"
    assert tailor.upload_name(profile, "Cover_Letter") == "Test_Q_Person_Longname_Cover_Letter.pdf"
    assert tailor.upload_name({"basics": {"name": ""}}) == "CV.pdf"
    assert len(tailor.upload_name({"basics": {"name": "A" * 80}})) <= 50


def test_cv_download_for_a_form_is_named_short(sandbox):
    profile = json.loads((sandbox / "data" / "base_cv.json").read_text("utf-8"))
    long = tailor.pdf_name(profile, "A Company With A Long Name", "Senior Artificial Intelligence Software Engineer")
    folder = output_dir() / "2026-10-30_Acme_Lead"
    folder.mkdir(parents=True)
    (folder / long).write_bytes(b"%PDF-1.4 test")
    (folder / "report.json").write_text(json.dumps({"company": "Acme", "role": "Lead", "pdf": long}), "utf-8")
    plain = LOCAL.get(f"/apps/{folder.name}/pdf")
    short = LOCAL.get(f"/apps/{folder.name}/pdf?upload=1")
    assert long in plain.headers["content-disposition"] and len(long) > 50
    assert tailor.upload_name(profile) in short.headers["content-disposition"]
    assert short.content == b"%PDF-1.4 test"
