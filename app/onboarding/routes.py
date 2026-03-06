import logging

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from ..models import db, TrackingLine, Partner, Account
from ..decorators import account_required
from ..phone_utils import get_available_numbers
from ..sync_utils import spawn_backfill, spawn_callrail_backfill
from ..twilio_service import (
    validate_twilio_credentials,
    create_ci_service,
    create_ci_operator,
)
from ..callrail_service import validate_callrail_credentials, fetch_callrail_accounts
from . import bp

logger = logging.getLogger(__name__)


@bp.route("/")
@login_required
@account_required
def wizard():
    if current_user.onboarding_completed:
        return redirect(url_for("dashboard.index"))

    account = db.session.get(Account, current_user.id)
    return render_template(
        "onboarding/wizard.html",
        account=account,
        active_page="onboarding",
    )


@bp.route("/validate-twilio", methods=["POST"])
@login_required
@account_required
def validate_twilio():
    data = request.get_json()
    sid = (data.get("account_sid") or "").strip()
    token = (data.get("auth_token") or "").strip()

    if not sid or not token:
        return jsonify({"error": "Both Account SID and Auth Token are required."}), 400

    if not validate_twilio_credentials(sid, token):
        return jsonify({"error": "Invalid Twilio credentials. Check your Account SID and Auth Token."}), 400

    account = db.session.get(Account, current_user.id)
    account.twilio_account_sid = sid
    account.twilio_auth_token_encrypted = token
    account.call_source = "twilio"

    if not account.twilio_service_sid:
        try:
            webhook_url = url_for("webhooks.twilio_ci_callback", _external=True)
            service_sid = create_ci_service(sid, token, webhook_url)
            account.twilio_service_sid = service_sid
            create_ci_operator(sid, token, service_sid)
        except Exception:
            logger.exception("Failed to provision Twilio CI during onboarding")

    db.session.commit()
    return jsonify({"success": True})


@bp.route("/validate-callrail", methods=["POST"])
@login_required
@account_required
def validate_callrail():
    data = request.get_json()
    api_key = (data.get("api_key") or "").strip()

    if not api_key:
        return jsonify({"error": "API key is required."}), 400

    if not validate_callrail_credentials(api_key):
        return jsonify({"error": "Invalid CallRail API key. Check and try again."}), 400

    try:
        accounts = fetch_callrail_accounts(api_key)
    except Exception:
        logger.exception("Failed to fetch CallRail accounts during onboarding")
        return jsonify({"error": "API key valid but failed to fetch accounts. Try again."}), 500

    if not accounts:
        return jsonify({"error": "No CallRail accounts found for this API key."}), 400

    account = db.session.get(Account, current_user.id)
    account.callrail_api_key_encrypted = api_key
    account.callrail_account_id = str(accounts[0]["id"])
    account.call_source = "callrail"
    db.session.commit()

    return jsonify({"success": True, "account_name": accounts[0]["name"]})


@bp.route("/fetch-numbers")
@login_required
@account_required
def fetch_numbers():
    account = db.session.get(Account, current_user.id)
    numbers = get_available_numbers(account)
    return jsonify(numbers)


@bp.route("/create-lines", methods=["POST"])
@login_required
@account_required
def create_lines():
    data = request.get_json()
    numbers = data.get("numbers", [])

    if not numbers:
        return jsonify({"error": "No numbers selected."}), 400

    created = 0
    for num in numbers:
        line = TrackingLine(
            account_id=current_user.id,
            partner_id=None,
            twilio_phone_number=num.get("number"),
            callrail_tracker_id=num.get("callrail_tracker_id"),
            callrail_tracking_number=num.get("callrail_tracking_number"),
            label=num.get("friendly_name", ""),
            active=True,
        )
        db.session.add(line)
        created += 1

    db.session.commit()
    return jsonify({"success": True, "lines_created": created})


@bp.route("/add-partner", methods=["POST"])
@login_required
@account_required
def add_partner():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    cost_per_lead = data.get("cost_per_lead", 0) or 0
    cost_per_call = data.get("cost_per_call", 0) or 0

    if not name:
        return jsonify({"error": "Partner name is required."}), 400

    partner = Partner(
        account_id=current_user.id,
        name=name,
        cost_per_lead=cost_per_lead,
        cost_per_call=cost_per_call,
    )
    db.session.add(partner)
    db.session.commit()

    return jsonify({"success": True, "id": partner.id, "name": partner.name})


@bp.route("/complete", methods=["POST"])
@login_required
@account_required
def complete():
    data = request.get_json() or {}
    backsync_days = data.get("backsync_days")

    account = db.session.get(Account, current_user.id)

    if backsync_days:
        days = min(max(int(backsync_days), 1), 30)
        if account.twilio_service_sid:
            spawn_backfill(account.id, days=days)
        elif account.callrail_account_id:
            spawn_callrail_backfill(account.id, days=days)

    account.onboarding_completed = True
    db.session.commit()

    redirect_url = url_for("dashboard.index")
    if backsync_days:
        from datetime import datetime, timedelta, timezone as tz
        today = datetime.now(tz.utc).date()
        date_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")
        redirect_url = url_for(
            "dashboard.index",
            importing="1",
            date_from=date_from,
            date_to=date_to,
        )

    return jsonify({"success": True, "redirect": redirect_url})
