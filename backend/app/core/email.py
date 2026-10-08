import smtplib
from email.message import EmailMessage

from app.core.security import settings


def send_password_reset_email(
    recipient_email: str,
    reset_token: str,
):
    print("========== EMAIL DEBUG ==========")
    print("SMTP HOST:", settings.smtp_host)
    print("SMTP PORT:", settings.smtp_port)
    print("SMTP USERNAME:", settings.smtp_username)
    print("SMTP FROM:", settings.smtp_from_email)
    print("=================================")

    reset_link = (
        "http://localhost:5173/reset-password"
        f"?token={reset_token}"
    )

    message = EmailMessage()

    message["Subject"] = "Reset Your Synthetic Monitor Password"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient_email

    message.set_content(
        f"""
Hello,

We received a request to reset your Synthetic Monitor password.

Click the link below to reset your password:

{reset_link}

This password reset link will expire in 30 minutes.

If you did not request a password reset, you can safely ignore this email.

Regards,
Synthetic Monitor Team
"""
    )

    print("Connecting to Gmail SMTP...")

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=30,
    ) as smtp:

        print("Connected to Gmail SMTP.")

        smtp.starttls()

        print("TLS connection established.")

        smtp.login(
            settings.smtp_username,
            settings.smtp_password,
        )

        print("SMTP login successful.")

        smtp.send_message(message)

        print("EMAIL SENT SUCCESSFULLY")
        print("=================================")