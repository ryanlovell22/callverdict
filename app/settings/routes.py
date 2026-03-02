import logging
import threading
from functools import wraps

from flask import render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user

from ..models import db
from ..twilio_service import (
    validate_twilio_credentials,
    create_ci_service,
    create_ci_operator,
)
from ..callrail_service import validate_callrail_credentials, fetch_callrail_accounts
from ..poll_service import run_full_sync
from . import bp

logger = logging.getLogger(__name__)


def account_required(f):
    """Block partner users from accessing these routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.user_type != "account":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _spawn_backfill(account_id, days=7):
    """Run a full Twilio sync in a background thread."""
    from ..models import Account
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            account = db.session.get(Account, account_id)
            if account:
                run_full_sync(account, days=days)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


@bp.route("/", methods=["GET", "POST"])
@login_required
@account_required
def index():
    account = current_user

    if request.method == "POST":
        sid = request.form.get("twilio_account_sid", "").strip()
        token_input = request.form.get("twilio_auth_token", "").strip()

        # If token field is blank or matches the masked placeholder, keep existing
        if not token_input or token_input.startswith("••••"):
            token = account.twilio_auth_token_encrypted
        else:
            token = token_input

        if not sid or not token:
            flash("Both Account SID and Auth Token are required.", "error")
            return redirect(url_for("settings.index"))

        # Validate credentials against Twilio API
        if not validate_twilio_credentials(sid, token):
            flash(
                "Invalid Twilio credentials. Please check your Account SID "
                "and Auth Token and try again.",
                "error",
            )
            return redirect(url_for("settings.index"))

        # Save credentials
        account.twilio_account_sid = sid
        account.twilio_auth_token_encrypted = token
        db.session.commit()

        if not account.twilio_service_sid:
            # First-time setup — provision CI service + operator
            try:
                webhook_url = url_for(
                    "webhooks.twilio_ci_callback", _external=True
                )
                service_sid = create_ci_service(sid, token, webhook_url)
                account.twilio_service_sid = service_sid
                create_ci_operator(sid, token, service_sid)
                db.session.commit()

                # Auto-backfill last 24 hours of calls in background
                _spawn_backfill(account.id, days=1)

                flash(
                    "Twilio connected and call analysis enabled. "
                    "Syncing your last 24 hours of calls in the background.",
                    "success",
                )
            except Exception:
                account.twilio_service_sid = None
                db.session.commit()
                logger.exception("Failed to provision Twilio CI")
                flash(
                    "Connected to Twilio. Note: automatic call intelligence setup failed "
                    "— calls will still be synced via polling.",
                    "warning",
                )
        else:
            # Just updating credentials
            db.session.commit()
            flash("Twilio credentials updated.", "success")

        return redirect(url_for("settings.index"))

    # GET — prepare display values
    masked_token = ""
    if account.twilio_auth_token_encrypted:
        masked_token = "••••" + account.twilio_auth_token_encrypted[-4:]

    connected = bool(account.twilio_service_sid)
    webhook_url = ""
    if connected:
        webhook_url = url_for("webhooks.twilio_ci_callback", _external=True)

    # CallRail connection status
    callrail_connected = bool(account.callrail_api_key_encrypted and account.callrail_account_id)
    callrail_account_name = ""
    if callrail_connected:
        try:
            cr_accounts = fetch_callrail_accounts(account.callrail_api_key_encrypted)
            for cr_acct in cr_accounts:
                if str(cr_acct["id"]) == str(account.callrail_account_id):
                    callrail_account_name = cr_acct["name"]
                    break
        except Exception:
            callrail_account_name = f"ID: {account.callrail_account_id}"

    masked_callrail_key = ""
    if account.callrail_api_key_encrypted:
        masked_callrail_key = "••••" + account.callrail_api_key_encrypted[-4:]

    callrail_webhook_url = ""
    if callrail_connected:
        callrail_webhook_url = url_for("webhooks.callrail_callback", _external=True)

    import pytz
    australian_timezones = [tz for tz in sorted(pytz.all_timezones) if tz.startswith('Australia/')]

    # Data for Upload section
    from ..models import TrackingLine
    lines = TrackingLine.query.filter_by(
        account_id=account.id, active=True
    ).all()

    return render_template(
        "settings/index.html",
        account_sid=account.twilio_account_sid or "",
        masked_token=masked_token,
        connected=connected,
        webhook_url=webhook_url,
        callrail_connected=callrail_connected,
        callrail_account_name=callrail_account_name,
        callrail_webhook_url=callrail_webhook_url,
        masked_callrail_key=masked_callrail_key,
        timezones=australian_timezones,
        current_timezone=account.timezone or "Australia/Adelaide",
        account=account,
        lines=lines,
        active_page="settings",
    )


@bp.route("/callrail", methods=["POST"])
@login_required
@account_required
def save_callrail():
    account = current_user
    api_key = request.form.get("callrail_api_key", "").strip()

    # If the field is blank or matches the masked placeholder, keep existing
    if not api_key or api_key.startswith("••••"):
        flash("Please enter your CallRail API key.", "error")
        return redirect(url_for("settings.index"))

    # Validate credentials
    if not validate_callrail_credentials(api_key):
        flash("Invalid CallRail API key. Please check and try again.", "error")
        return redirect(url_for("settings.index"))

    # Fetch accounts to get the account ID
    try:
        accounts = fetch_callrail_accounts(api_key)
    except Exception:
        logger.exception("Failed to fetch CallRail accounts")
        flash("API key is valid but failed to fetch accounts. Please try again.", "error")
        return redirect(url_for("settings.index"))

    if not accounts:
        flash("No CallRail accounts found for this API key.", "error")
        return redirect(url_for("settings.index"))

    # If single account, save automatically; if multiple, use the first one
    account.callrail_api_key_encrypted = api_key
    account.callrail_account_id = str(accounts[0]["id"])
    db.session.commit()

    flash(f"CallRail connected — Account: {accounts[0]['name']}", "success")
    return redirect(url_for("settings.index"))


@bp.route("/timezone", methods=["POST"])
@login_required
@account_required
def save_timezone():
    tz = request.form.get("timezone", "Australia/Adelaide").strip()
    import pytz
    if tz in pytz.all_timezones:
        current_user.timezone = tz
        db.session.commit()
        flash("Timezone updated.", "success")
    else:
        flash("Invalid timezone.", "error")
    return redirect(url_for("settings.index"))


@bp.route("/sync", methods=["POST"])
@login_required
@account_required
def sync_calls():
    """Manually trigger a Twilio call sync."""
    account = current_user

    if not account.twilio_account_sid or not account.twilio_auth_token_encrypted:
        flash("Please connect Twilio first.", "error")
        return redirect(url_for("settings.index"))

    if not account.twilio_service_sid:
        flash("Twilio CI is not configured. Please reconnect Twilio.", "error")
        return redirect(url_for("settings.index"))

    days = request.form.get("sync_days", 1, type=int)
    days = min(max(days, 1), 30)  # Clamp between 1 and 30

    _spawn_backfill(account.id, days=days)

    flash(
        f"Syncing your last {days} day{'s' if days != 1 else ''} of Twilio calls in the background. "
        "Refresh the dashboard in a minute to see results.",
        "success",
    )
    return redirect(url_for("settings.index"))
