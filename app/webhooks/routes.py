import json
import logging
from datetime import datetime, timezone

from flask import request, jsonify, current_app

from ..models import db, Call, Account, TrackingLine
from ..twilio_service import fetch_transcript_text, fetch_operator_results
from ..ai_classifier import classify_transcript
from . import bp

logger = logging.getLogger(__name__)


def _check_usage_limit(account):
    """Check if account has reached their call processing limit.
    Returns True if limit reached, False if OK to process.
    """
    if account.plan_calls_used is not None and account.plan_calls_limit is not None:
        if account.plan_calls_used >= account.plan_calls_limit:
            return True
    return False


def _increment_usage(account):
    """Increment the account's usage counter."""
    if account.plan_calls_used is None:
        account.plan_calls_used = 0
    account.plan_calls_used += 1


@bp.route("/twilio-ci", methods=["POST"])
def twilio_ci_callback():
    """Receive webhook from Twilio Conversational Intelligence when
    transcription and operator analysis is complete."""

    data = request.json or request.form.to_dict()
    logger.info("Twilio CI webhook received: %s", json.dumps(data, default=str))

    transcript_sid = data.get("TranscriptSid") or data.get("transcript_sid")
    if not transcript_sid:
        return jsonify({"error": "No TranscriptSid provided"}), 400

    # Find the call record by transcript_sid
    call = Call.query.filter_by(transcript_sid=transcript_sid).first()
    if not call:
        logger.warning("No call found for transcript_sid=%s (will retry)", transcript_sid)
        return jsonify({"status": "accepted, will retry"}), 202

    account = db.session.get(Account, call.account_id)
    if not account or not account.twilio_account_sid:
        return jsonify({"error": "Account not configured"}), 400

    # Check usage limit
    if _check_usage_limit(account):
        call.status = "limit_reached"
        db.session.commit()
        logger.info("Account %s has reached usage limit", account.id)
        return jsonify({"status": "limit_reached"}), 200

    try:
        # Fetch operator results from Twilio
        operator_results = fetch_operator_results(
            account.twilio_account_sid,
            account.twilio_auth_token_encrypted,
            transcript_sid,
        )

        if operator_results:
            call.classification = operator_results.get("classification")
            call.confidence = operator_results.get("confidence")
            call.summary = operator_results.get("summary")
            call.service_type = operator_results.get("service_type")
            call.urgent = operator_results.get("urgent", False)
            call.customer_name = operator_results.get("customer_name")
            call.customer_address = operator_results.get("customer_address")
            call.booking_time = operator_results.get("booking_time")

            # Voicemail calls are missed from the partner's perspective
            if call.classification == "VOICEMAIL":
                call.call_outcome = "voicemail"

        # Fetch full transcript text
        transcript_text = fetch_transcript_text(
            account.twilio_account_sid,
            account.twilio_auth_token_encrypted,
            transcript_sid,
        )
        if transcript_text:
            call.full_transcript = transcript_text

        call.status = "completed"
        call.analysed_at = datetime.now(timezone.utc)
        _increment_usage(account)
        db.session.commit()

        logger.info(
            "Call %s analysed: %s", call.id, call.classification
        )
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error processing webhook for transcript %s", transcript_sid)
        call.status = "failed"
        db.session.commit()
        # Return 500 so Twilio retries on genuine errors
        return jsonify({"error": str(e)}), 500


@bp.route("/callrail", methods=["POST"])
def callrail_callback():
    """Receive post-call webhook from CallRail."""

    data = request.json or {}
    logger.info("CallRail webhook received: %s", json.dumps(data, default=str))

    call_id = data.get("id")
    if not call_id:
        return jsonify({"error": "No call id provided"}), 400

    answered = data.get("answered", False)
    duration = int(data.get("duration") or 0)
    customer_phone = data.get("customer_phone_number", "")
    tracking_phone = data.get("tracking_phone_number", "")
    recording_url = data.get("recording")
    transcription = data.get("transcription")
    start_time = data.get("start_time")

    # Find tracking line by CallRail tracking number
    tracking_line = TrackingLine.query.filter_by(
        callrail_tracking_number=tracking_phone, active=True
    ).first()

    if not tracking_line:
        logger.warning(
            "No tracking line found for CallRail number %s", tracking_phone
        )
        # Still return 200 so CallRail doesn't keep retrying
        return jsonify({"status": "ignored, unknown tracking number"}), 200

    account = db.session.get(Account, tracking_line.account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 400

    # Check usage limit
    if _check_usage_limit(account):
        logger.info("Account %s has reached usage limit", account.id)
        return jsonify({"status": "limit_reached"}), 200

    # Dedup: check callrail_call_id doesn't already exist for this account
    existing = Call.query.filter_by(
        account_id=account.id, callrail_call_id=str(call_id)
    ).first()
    if existing:
        logger.info("Duplicate CallRail call %s, skipping", call_id)
        return jsonify({"status": "duplicate"}), 200

    # Skip very short calls (likely accidental dials)
    if duration < 3:
        logger.info("CallRail call %s too short (%ds), skipping", call_id, duration)
        return jsonify({"status": "skipped, too short"}), 200

    # Parse call date from start_time (ISO format)
    call_date = None
    if start_time:
        try:
            call_date = datetime.fromisoformat(start_time)
        except (ValueError, TypeError):
            call_date = datetime.now(timezone.utc)

    # Determine call outcome and status
    if not answered or not recording_url:
        # Missed call — no recording to process
        call = Call(
            account_id=account.id,
            tracking_line_id=tracking_line.id,
            callrail_call_id=str(call_id),
            caller_number=customer_phone,
            call_duration=duration,
            call_date=call_date,
            recording_url=recording_url,
            source="callrail",
            call_outcome="missed",
            status="completed",
        )
        db.session.add(call)
        _increment_usage(account)
        db.session.commit()
        logger.info("CallRail missed call %s saved", call_id)
        return jsonify({"status": "ok"}), 200

    # Answered call with a recording
    call = Call(
        account_id=account.id,
        tracking_line_id=tracking_line.id,
        callrail_call_id=str(call_id),
        caller_number=customer_phone,
        call_duration=duration,
        call_date=call_date,
        recording_url=recording_url,
        source="callrail",
        call_outcome="answered",
        status="processing",
    )
    db.session.add(call)
    db.session.flush()

    if transcription:
        # Transcript already available — classify immediately
        try:
            biz_name = tracking_line.label or tracking_line.partner_name
            results = classify_transcript(transcription, business_name=biz_name)
            call.full_transcript = transcription
            call.classification = results.get("classification")
            call.confidence = results.get("confidence")
            call.summary = results.get("summary")
            call.service_type = results.get("service_type")
            call.urgent = results.get("urgent", False)
            call.customer_name = results.get("customer_name")
            call.customer_address = results.get("customer_address")
            call.booking_time = results.get("booking_time")
            call.analysed_at = datetime.now(timezone.utc)
            call.status = "completed"

            if call.classification == "VOICEMAIL":
                call.call_outcome = "voicemail"

            logger.info(
                "CallRail call %s classified: %s", call_id, call.classification
            )
        except Exception as e:
            logger.exception("Failed to classify CallRail call %s: %s", call_id, e)
            call.status = "failed"
    # else: no transcript, status stays "processing" for cron to pick up

    _increment_usage(account)
    db.session.commit()
    return jsonify({"status": "ok"}), 200


@bp.route("/stripe", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    import stripe

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return jsonify({"error": "Webhook not configured"}), 500

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        logger.warning("Invalid Stripe webhook payload")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        logger.warning("Invalid Stripe webhook signature")
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event["type"]
    event_data = event["data"]["object"]

    logger.info("Stripe webhook received: %s", event_type)

    try:
        if event_type == "checkout.session.completed":
            from ..stripe_service import handle_checkout_completed
            handle_checkout_completed(event_data)
        elif event_type == "invoice.paid":
            from ..stripe_service import handle_invoice_paid
            handle_invoice_paid(event_data)
        elif event_type == "customer.subscription.updated":
            from ..stripe_service import handle_subscription_updated
            handle_subscription_updated(event_data)
        elif event_type == "customer.subscription.deleted":
            from ..stripe_service import handle_subscription_deleted
            handle_subscription_deleted(event_data)
        else:
            logger.info("Unhandled Stripe event type: %s", event_type)
    except Exception:
        logger.exception("Error processing Stripe webhook %s", event_type)
        return jsonify({"error": "Processing error"}), 500

    return jsonify({"status": "ok"}), 200
