"""A CV or cover letter attached to a form goes as Company_RoleInitials.pdf: some sites refuse a long file name,
with a pop-up that stops the form."""
import json

from fastapi.testclient import TestClient

from app import tailor
from app.config import output_dir
from app.main import app

LOCAL = TestClient(app, base_url="http://127.0.0.1:8765")


def test_upload_name_is_company_and_role_initials():
    assert tailor.upload_name("Valtech", "AI-Technical Lead") == "Valtech_AITL.pdf"
    assert tailor.upload_name("Franklin Templeton", "Senior AI Software Engineer") == "FT_SAISE.pdf"
    assert tailor.upload_name("JP Morgan and Chase", "Lead AI Engineer", letter=True) == "JPMC_LAIE_CL.pdf"
    assert tailor.upload_name("", "") == "CV.pdf"
    assert len(tailor.upload_name("A Very Long Company Name " * 5, "Principal Staff Engineer " * 5, letter=True)) <= 50


def test_cv_download_for_a_form_is_named_short(sandbox):
    profile = json.loads((sandbox / "data" / "base_cv.json").read_text("utf-8"))
    long = tailor.pdf_name(profile, "Franklin Templeton", "Senior AI Software Engineer")
    folder = output_dir() / "2026-10-30_FT_SAISE"
    folder.mkdir(parents=True)
    (folder / long).write_bytes(b"%PDF-1.4 test")
    (folder / "report.json").write_text(json.dumps({"company": "Franklin Templeton", "role": "Senior AI Software Engineer",
                                                    "pdf": long}), "utf-8")
    assert long in LOCAL.get(f"/apps/{folder.name}/pdf").headers["content-disposition"]
    short = LOCAL.get(f"/apps/{folder.name}/pdf?upload=1")
    assert "FT_SAISE.pdf" in short.headers["content-disposition"] and short.content == b"%PDF-1.4 test"
