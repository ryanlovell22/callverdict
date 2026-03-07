import logging
import resend
from flask import current_app

logger = logging.getLogger(__name__)


def send_email(to, subject, html):
    """Send a transactional email via Resend."""
    api_key = current_app.config.get("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not configured — email not sent")
        return False

    resend.api_key = api_key
    try:
        resend.Emails.send({
            "from": "CallOutcome <onboarding@resend.dev>",
            "to": [to],
            "subject": subject,
            "html": html,
        })
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False
