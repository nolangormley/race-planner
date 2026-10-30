import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import auth_routes
from src.api.auth_routes import (
    hash_password, make_reset_token, new_password_error, user_for_reset_token, verify_password,
)
from src.api.database import Base, User

SECRET = "test-secret"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def user(db):
    user = User(email="runner@example.com", password=hash_password("old-password"), name="Runner")
    db.add(user)
    db.commit()
    return user


def test_token_finds_its_user(db, user):
    token = make_reset_token(user, SECRET)
    assert user_for_reset_token(token, SECRET, db).id == user.id


def test_token_stops_working_once_password_changes(db, user):
    token = make_reset_token(user, SECRET)
    user.password = hash_password("new-password")
    db.commit()
    assert user_for_reset_token(token, SECRET, db) is None


def test_token_signed_with_another_key_is_rejected(db, user):
    token = make_reset_token(user, "some-other-key")
    assert user_for_reset_token(token, SECRET, db) is None


def test_tampered_token_is_rejected(db, user):
    token = make_reset_token(user, SECRET)
    assert user_for_reset_token(token[:-2] + "xx", SECRET, db) is None
    assert user_for_reset_token("", SECRET, db) is None


def test_expired_token_is_rejected(db, user, monkeypatch):
    token = make_reset_token(user, SECRET)
    monkeypatch.setattr(auth_routes, "RESET_TOKEN_MAX_AGE", -1)
    assert user_for_reset_token(token, SECRET, db) is None


def test_token_does_not_carry_a_plaintext_password(db):
    # Legacy rows can still hold a plaintext password; the token payload is
    # only signed, so it must not reveal it.
    legacy = User(email="legacy@example.com", password="hunter22", name="Legacy")
    db.add(legacy)
    db.commit()
    token = make_reset_token(legacy, SECRET)
    payload = auth_routes._reset_serializer(SECRET).loads(token)
    assert "hunter22" not in str(payload)
    assert user_for_reset_token(token, SECRET, db).id == legacy.id


def test_new_password_rules():
    assert new_password_error("short") is not None
    assert new_password_error("x" * 73) is not None
    assert new_password_error("long enough") is None


# ---- Routes ----

@pytest.fixture
def client(db, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import app

    sent = []
    monkeypatch.setattr(auth_routes, "send_email", lambda to, subject, body: sent.append((to, body)))
    app.dependency_overrides[auth_routes.get_db] = lambda: db
    monkeypatch.setattr(app.state, "secret_key", SECRET, raising=False)
    client = TestClient(app)
    client.sent = sent
    yield client
    app.dependency_overrides.clear()


def test_forgot_password_emails_a_link_that_resets_the_password(client, db, user):
    response = client.post("/forgot-password", data={"email": "runner@example.com"})
    assert response.status_code == 200
    assert "reset link is on its way" in response.text
    assert len(client.sent) == 1
    to, body = client.sent[0]
    assert to == "runner@example.com"
    token = body.split("token=")[1].split()[0]

    assert "Choose a new password" in client.get(f"/reset-password?token={token}").text

    response = client.post(
        "/reset-password",
        data={"token": token, "password": "brand-new-pass", "confirm_password": "brand-new-pass"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/login?reset=1"
    db.refresh(user)
    assert verify_password("brand-new-pass", user.password)

    # The link is single-use.
    response = client.post(
        "/reset-password",
        data={"token": token, "password": "another-pass", "confirm_password": "another-pass"},
    )
    assert "expired or was already used" in response.text


def test_forgot_password_gives_the_same_answer_for_unknown_emails(client, user):
    response = client.post("/forgot-password", data={"email": "nobody@example.com"})
    assert "reset link is on its way" in response.text
    assert client.sent == []


def test_reset_rejects_mismatched_passwords(client, db, user):
    token = make_reset_token(user, SECRET)
    response = client.post(
        "/reset-password",
        data={"token": token, "password": "brand-new-pass", "confirm_password": "different-pass"},
    )
    assert "don&#39;t match" in response.text
    db.refresh(user)
    assert verify_password("old-password", user.password)


def test_change_password_needs_the_current_one(client, db, user):
    client.post("/login", data={"username": "runner@example.com", "password": "old-password"}, follow_redirects=False)

    response = client.post("/api/password", json={"current_password": "wrong", "new_password": "brand-new-pass"})
    assert response.status_code == 400

    response = client.post("/api/password", json={"current_password": "old-password", "new_password": "brand-new-pass"})
    assert response.status_code == 200
    db.refresh(user)
    assert verify_password("brand-new-pass", user.password)


def test_change_password_requires_login(client):
    response = client.post("/api/password", json={"current_password": "a", "new_password": "brand-new-pass"})
    assert response.status_code == 401
