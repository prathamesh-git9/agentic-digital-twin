from __future__ import annotations

from pathlib import Path

import yaml

from digital_twin.config import Settings
from digital_twin.mailer import GmailSMTPSender

ROOT = Path(__file__).parents[1]


def test_render_blueprint_keeps_model_key_out_of_source() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    variables = {row["key"]: row for row in service["envVars"]}

    assert service["healthCheckPath"] == "/api/health"
    assert service["startCommand"].endswith("--port $PORT")
    assert variables["TWIN_PROVIDER"]["value"] == "openai-compatible"
    assert variables["TWIN_LLM_BASE_URL"]["value"] == "https://api.openai.com/v1"
    assert variables["TWIN_LLM_MODEL"]["value"] == "gpt-5.6-luna"
    assert variables["TWIN_MAX_OUTPUT_TOKENS"]["value"] == "500"
    assert variables["TWIN_TOOL_TIMEOUT_SECONDS"]["value"] == "25"
    assert variables["TWIN_LLM_API_KEY"] == {
        "key": "TWIN_LLM_API_KEY",
        "sync": False,
    }


def test_owner_provisioned_environment_names_map_exactly(monkeypatch) -> None:
    values = {
        "TWIN_SMTP_HOST": "smtp.gmail.com",
        "TWIN_SMTP_PORT": "587",
        "TWIN_SMTP_STARTTLS": "true",
        "TWIN_SMTP_USERNAME": "owner@gmail.com",
        "TWIN_SMTP_PASSWORD": "offline-dummy-password",
        "TWIN_FROM_EMAIL": "owner@gmail.com",
        "TWIN_FROM_NAME": "Test Owner",
        "TWIN_AUTOSEND": "true",
        "TWIN_FANOUT_UNSELECTED": "true",
        "TWIN_FANOUT_MAX": "3",
        "TWIN_INFERRED_SEND_MAX": "2",
        "TWIN_DAILY_SEND_CAP": "7",
        "TWIN_LINKEDIN_AUTO": "true",
        "TWIN_PUSHOVER_ENABLED": "true",
        "TWIN_PUSHOVER_USER": "dummy-user",
        "TWIN_PUSHOVER_TOKEN": "dummy-token",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings(
        _env_file=None,
        environment="test",
        profile_path=ROOT / "data" / "profile.yaml",
    )

    assert settings.smtp_ready is True
    assert settings.from_name == "Test Owner"
    assert settings.autosend is True
    assert settings.fanout_unselected is True
    assert settings.fanout_max == 3
    assert settings.inferred_send_max == 2
    assert settings.daily_send_cap == 7
    assert settings.linkedin_auto is True
    assert settings.pushover_enabled is True


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_called = False
        self.sent = 0
        self.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, *, context: object) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_called = bool(username and password)

    def send_message(self, message: object) -> None:
        self.sent += 1


async def test_gmail_sender_uses_587_starttls_and_self_test_never_sends(
    monkeypatch,
) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("digital_twin.mailer.smtplib.SMTP", FakeSMTP)
    settings = Settings(
        _env_file=None,
        environment="test",
        profile_path=ROOT / "data" / "profile.yaml",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="owner@gmail.com",
        smtp_password="offline-dummy-password",
        from_email="owner@gmail.com",
        from_name="Test Owner",
    )
    sender = GmailSMTPSender(settings)

    check = await sender.self_test()

    assert check.ok is True
    assert check.authenticated_as is None
    assert len(FakeSMTP.instances) == 1
    assert FakeSMTP.instances[0].host == "smtp.gmail.com"
    assert FakeSMTP.instances[0].port == 587
    assert FakeSMTP.instances[0].starttls_called is True
    assert FakeSMTP.instances[0].login_called is True
    assert FakeSMTP.instances[0].sent == 0
