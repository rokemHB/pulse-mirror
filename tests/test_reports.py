from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pytest

from pulse_mirror import notify
from pulse_mirror.demo.generator import generate
from pulse_mirror.reports import export, morning, pdf
from pulse_mirror.sync.service import SyncResult

END = date(2025, 6, 30)


@pytest.fixture
def demo(conn):
    generate(conn, END, days=90)
    return conn


def test_csv_exports(demo):
    rows = list(csv.DictReader(io.StringIO(export.dataset_csv(demo, "metrics"))))
    assert rows and {"date", "metric_key", "source", "value_num"} <= set(rows[0])
    ratings = list(csv.DictReader(io.StringIO(export.dataset_csv(demo, "ratings"))))
    assert ratings[0]["scale_version"] == "1"
    with zipfile.ZipFile(io.BytesIO(export.all_zip(demo))) as zf:
        assert sorted(zf.namelist()) == sorted(f"pulse-mirror-{d}.csv" for d in export.DATASETS)


def test_weekly_pdf(demo, tmp_path):
    monday = date(2025, 6, 23)
    path = pdf.write_weekly_report(demo, monday, tmp_path)
    assert path.name == "pulse-mirror-2025-W26.pdf"
    assert path.read_bytes().startswith(b"%PDF")


def test_weekly_pdf_on_empty_db(conn):
    assert pdf.build_weekly_report(conn, date(2025, 6, 23)).startswith(b"%PDF")


def test_morning_summary(demo):
    subject, text = morning.compose(demo, END, SyncResult("ok", 3, 1, "ok"))
    assert subject.startswith("pulse-mirror Mon 30 Jun") and "HRV" in subject
    assert "Resting heart rate" in text and "vs 28d" in text


def test_morning_summary_reports_failures(demo):
    subject, text = morning.compose(demo, END, SyncResult("auth_required", 0, 0, "expired"))
    assert "failed" in subject and "login garmin" in text


def test_notifier_from_env(monkeypatch):
    monkeypatch.delenv("PULSE_SMTP_HOST", raising=False)
    assert notify.from_env() is None
    monkeypatch.setenv("PULSE_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("PULSE_NOTIFY_TO", "me@example.invalid")
    monkeypatch.setenv("PULSE_SMTP_USER", "me@example.invalid")
    n = notify.from_env()
    assert (
        isinstance(n, notify.EmailNotifier) and n.port == 587 and n.sender == "me@example.invalid"
    )


def test_email_is_sent_over_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, msg):
            sent["subject"] = msg["Subject"]

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    notify.EmailNotifier("h", 587, "u", "p", "u", "to").send("Hi", "text")
    assert sent == {"host": ("h", 587), "tls": True, "login": "u", "subject": "Hi"}
