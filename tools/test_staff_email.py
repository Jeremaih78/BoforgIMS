"""Local verification with .env ignored, real SMTP blocked and only the test DB reachable."""

import os
import smtplib
import socket
import sys
from pathlib import Path

import dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    ims = (root / "tasker").is_dir()
    port = int(os.environ.get("STAFF_EMAIL_TEST_PORT", "55457"))
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.staff_email_test_settings"
        if ims
        else "config.settings.staff_email_test",
        DJANGO_SECRET_KEY="synthetic-local-tests-only",
        DJANGO_DEBUG="true",
        POSTGRES_PASSWORD="unused-test-value",
        TASKER_REPORT_EMAIL_LIVE_ENABLED="false",
        TASKER_CLOUD_EMAIL_ENABLED="false",
        COMMUNICATIONS_SEND_ENABLED="false",
    )
    dotenv.load_dotenv = lambda *args, **kwargs: False
    connect = socket.socket.connect

    def local_database_only(sock, address):
        if isinstance(address, tuple) and (
            address[0] not in ("127.0.0.1", "localhost", "::1") or address[1] != port
        ):
            raise RuntimeError("Only the disposable test database is reachable")
        return connect(sock, address)

    socket.socket.connect = local_database_only

    def no_smtp(*args, **kwargs):
        raise RuntimeError("Real SMTP is blocked; inject a mock transport")

    smtplib.SMTP = no_smtp
    smtplib.SMTP_SSL = no_smtp
    sys.path.insert(0, str(root))
    os.chdir(root)
    from django.core.management import execute_from_command_line  # noqa: E402

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
