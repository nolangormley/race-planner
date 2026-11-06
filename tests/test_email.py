"""send_email over a stand-in SMTP server: the text, and the HTML version as its alternative."""
import pytest

from src.api.services import email


class FakeSMTP:
    outbox = []

    def __init__(self, host, port, timeout):
        self.address = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        FakeSMTP.outbox.append(message)


@pytest.fixture
def outbox(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "app@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.outbox = []
    return FakeSMTP.outbox


def test_the_html_goes_as_the_alternative_to_the_text(outbox):
    email.send_email("runner@example.com", "Your week", "plain words", "<p>rich words</p>")
    message = outbox[0]
    assert message.get_content_type() == "multipart/alternative"
    assert [(part.get_content_type(), part.get_content().strip()) for part in message.iter_parts()] == [
        ("text/plain", "plain words"), ("text/html", "<p>rich words</p>"),
    ]
    assert (message["From"], message["To"], message["Subject"]) == ("app@example.test", "runner@example.com", "Your week")


def test_without_html_the_message_is_text_only(outbox):
    email.send_email("runner@example.com", "Your week", "plain words")
    assert outbox[0].get_content_type() == "text/plain"
    assert outbox[0].get_content().strip() == "plain words"
