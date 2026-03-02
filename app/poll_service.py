"""Shared polling functions for fetching Twilio calls.

Used by both the cron script (scripts/poll_twilio.py) and the web app
(settings sync, auto-backfill on first connect).
"""

import logging
from datetime import datetime, timedelta, timezone

from .models import db, Account, Call, TrackingLine
from .twilio_service import fetch_recordings, fetch_calls, get_call_details
from .ai_classifier import transcribe_recording, classify_transcript

logger = logging.getLogger(__name__)

# Minimum recording duration to process (seconds).
MIN_RECORDING_SECONDS = 3


def _parse_booking_date(value):
    """Parse an ISO 8601 booking_date string into a datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _increment_usage(account):
    """Increment the account's usage counter."""
    if account.plan_calls_used is None:
        account.plan_calls_used = 0
    account.plan_calls_used += 1


def poll_account(account, since):
    """Fetch new recordings for an account and classify via OpenAI."""
    if not account.twilio_account_sid or not account.twilio_auth_token_encrypted:
        logger.info("Account %s: No Twilio credentials, skipping", account.id)
        return 0

    logger.info(
        "Account %s: Fetching recordings since %s", account.id, since.isoformat()
    )

    recordings = fetch_recordings(
        account.twilio_account_sid,
        account.twilio_auth_token_encrypted,
        date_after=since,
    )

    new_count = 0
    for rec in recordings:
        recording_sid = rec.get("sid")

        # Skip if already in database
        existing = Call.query.filter_by(
            account_id=account.id, twilio_recording_sid=recording_sid
        ).first()
        if existing:
            continue

        call_sid = rec.get("call_sid")
        duration = int(rec.get("duration", 0))

        # Skip very short recordings (likely accidental dials)
        if duration < MIN_RECORDING_SECONDS:
            continue

        # Get call details to find the phone numbers
        try:
            call_details = get_call_details(
                account.twilio_account_sid,
                account.twilio_auth_token_encrypted,
                call_sid,
            )
        except Exception as e:
            logger.warning("Failed to get call details for %s: %s", call_sid, e)
            continue

        to_number = call_details.get("to", "")
        from_number = call_details.get("from", "")

        # Match to a tracking line — skip if unmatched
        tracking_line = TrackingLine.query.filter_by(
            account_id=account.id, twilio_phone_number=to_number, active=True
        ).first()
        if not tracking_line:
            continue

        recording_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{account.twilio_account_sid}/Recordings/{recording_sid}"
        )

        # Parse the date
        date_str = rec.get("date_created")
        call_date = None
        if date_str:
            try:
                call_date = datetime.strptime(
                    date_str, "%a, %d %b %Y %H:%M:%S %z"
                )
            except ValueError:
                call_date = datetime.now(timezone.utc)

        # Create call record
        call = Call(
            account_id=account.id,
            tracking_line_id=tracking_line.id if tracking_line else None,
            twilio_call_sid=call_sid,
            twilio_recording_sid=recording_sid,
            caller_number=from_number,
            call_duration=duration,
            call_date=call_date,
            recording_url=recording_url,
            source="twilio",
            status="processing",
            call_outcome="answered",
        )
        db.session.add(call)
        db.session.flush()  # Get the call ID

        # Check usage limit before processing (costs ~$0.03-0.04/call)
        if account.at_usage_limit:
            call.status = "limit_reached"
            logger.info(
                "Account %s at limit, recording %s saved as limit_reached",
                account.id, recording_sid,
            )
        else:
            # Transcribe + classify via OpenAI
            try:
                transcript_text = transcribe_recording(
                    recording_url + ".mp3",
                    auth=(account.twilio_account_sid, account.twilio_auth_token_encrypted),
                )
                call.full_transcript = transcript_text

                biz_name = (tracking_line.label or tracking_line.partner_name) if tracking_line else None
                result = classify_transcript(transcript_text, business_name=biz_name, call_date=call_date)

                call.classification = result.get("classification")
                call.confidence = result.get("confidence")
                call.summary = result.get("summary")
                call.service_type = result.get("service_type")
                call.urgent = result.get("urgent", False)
                call.customer_name = result.get("customer_name")
                call.customer_address = result.get("customer_address")
                call.booking_time = result.get("booking_time")
                call.booking_date = _parse_booking_date(result.get("booking_date"))
                call.analysed_at = datetime.now(timezone.utc)
                call.status = "completed"

                if call.classification == "VOICEMAIL":
                    call.call_outcome = "voicemail"

                _increment_usage(account)
                logger.info(
                    "Recording %s classified: %s (confidence: %s)",
                    recording_sid, call.classification, call.confidence,
                )
            except Exception as e:
                logger.error(
                    "Failed to process recording %s: %s", recording_sid, e
                )
                call.status = "failed"

        new_count += 1

    db.session.commit()
    return new_count


def poll_missed_calls(account, since):
    """Fetch missed calls (no-answer, busy, canceled) and create records."""
    if not account.twilio_account_sid or not account.twilio_auth_token_encrypted:
        return 0

    logger.info(
        "Account %s: Fetching missed calls since %s", account.id, since.isoformat()
    )

    missed_calls = fetch_calls(
        account.twilio_account_sid,
        account.twilio_auth_token_encrypted,
        status_list=["no-answer", "busy", "canceled"],
        date_after=since,
    )

    new_count = 0
    for twilio_call in missed_calls:
        call_sid = twilio_call.get("sid")

        # Dedup on twilio_call_sid
        existing = Call.query.filter_by(
            account_id=account.id, twilio_call_sid=call_sid
        ).first()
        if existing:
            continue

        to_number = twilio_call.get("to", "")
        from_number = twilio_call.get("from", "")
        duration = int(twilio_call.get("duration") or 0)

        # Match to a tracking line — skip if unmatched
        tracking_line = TrackingLine.query.filter_by(
            account_id=account.id, twilio_phone_number=to_number, active=True
        ).first()
        if not tracking_line:
            continue

        # Parse the date
        date_str = twilio_call.get("date_created")
        call_date = None
        if date_str:
            try:
                call_date = datetime.strptime(
                    date_str, "%a, %d %b %Y %H:%M:%S %z"
                )
            except ValueError:
                call_date = datetime.now(timezone.utc)

        call = Call(
            account_id=account.id,
            tracking_line_id=tracking_line.id,
            twilio_call_sid=call_sid,
            caller_number=from_number,
            call_duration=duration,
            call_date=call_date,
            source="twilio",
            call_outcome="missed",
            status="completed",
        )
        db.session.add(call)
        new_count += 1

    db.session.commit()
    return new_count


def poll_short_answered_calls(account, since):
    """Fetch completed calls that have no recording in our DB.

    Catches very short answered calls where Twilio didn't create a recording.
    These fall through both poll_account (no recording) and poll_missed_calls
    (status is 'completed', not 'no-answer').

    Note: Twilio creates separate call legs for forwarded calls (parent +
    child), each with a different call SID. The recording is on the child
    leg, but the Calls API returns the parent. So we dedup by caller number
    + time window, not just call SID.
    """
    if not account.twilio_account_sid or not account.twilio_auth_token_encrypted:
        return 0

    logger.info(
        "Account %s: Fetching short answered calls since %s",
        account.id, since.isoformat(),
    )

    completed_calls = fetch_calls(
        account.twilio_account_sid,
        account.twilio_auth_token_encrypted,
        status_list=["completed"],
        date_after=since,
    )

    new_count = 0
    for twilio_call in completed_calls:
        call_sid = twilio_call.get("sid")
        duration = int(twilio_call.get("duration") or 0)

        # Only interested in short calls — longer ones already come in via
        # recordings in poll_account(). Using a generous threshold to avoid
        # missing edge cases where recording duration differs from call duration.
        if duration > 20:
            continue

        # Dedup on twilio_call_sid
        existing = Call.query.filter_by(
            account_id=account.id, twilio_call_sid=call_sid
        ).first()
        if existing:
            continue

        from_number = twilio_call.get("from", "")
        to_number = twilio_call.get("to", "")

        # Parse the date
        date_str = twilio_call.get("date_created")
        call_date = None
        if date_str:
            try:
                call_date = datetime.strptime(
                    date_str, "%a, %d %b %Y %H:%M:%S %z"
                )
            except ValueError:
                call_date = datetime.now(timezone.utc)

        # Dedup by caller + time window. Forwarded calls create two legs
        # with different SIDs but same caller and near-identical timestamps.
        if call_date:
            window = timedelta(minutes=3)
            near_dup = Call.query.filter(
                Call.account_id == account.id,
                Call.caller_number == from_number,
                Call.call_date.between(call_date - window, call_date + window),
            ).first()
            if near_dup:
                continue

        # Match to a tracking line — skip if unmatched
        tracking_line = TrackingLine.query.filter_by(
            account_id=account.id, twilio_phone_number=to_number, active=True
        ).first()
        if not tracking_line:
            continue

        call = Call(
            account_id=account.id,
            tracking_line_id=tracking_line.id,
            twilio_call_sid=call_sid,
            caller_number=from_number,
            call_duration=duration,
            call_date=call_date,
            source="twilio",
            call_outcome="missed",
            status="completed",
            summary="Short or unanswered call.",
        )
        db.session.add(call)
        new_count += 1

    db.session.commit()
    return new_count


def retry_failed_submissions(account):
    """Retry failed calls via OpenAI (up to 3 retries, Twilio calls only)."""
    if not account.twilio_account_sid or not account.twilio_auth_token_encrypted:
        return 0

    if account.at_usage_limit:
        return 0

    failed_calls = Call.query.filter_by(
        account_id=account.id,
        status="failed",
        source="twilio",
    ).filter(Call.retry_count < 3).all()

    retried = 0
    for call in failed_calls:
        if not call.recording_url:
            continue

        call.retry_count = (call.retry_count or 0) + 1
        try:
            transcript_text = transcribe_recording(
                call.recording_url + ".mp3",
                auth=(account.twilio_account_sid, account.twilio_auth_token_encrypted),
            )
            call.full_transcript = transcript_text

            tracking_line = call.tracking_line
            biz_name = (tracking_line.label or tracking_line.partner_name) if tracking_line else None
            result = classify_transcript(transcript_text, business_name=biz_name, call_date=call.call_date)

            call.classification = result.get("classification")
            call.confidence = result.get("confidence")
            call.summary = result.get("summary")
            call.service_type = result.get("service_type")
            call.urgent = result.get("urgent", False)
            call.customer_name = result.get("customer_name")
            call.customer_address = result.get("customer_address")
            call.booking_time = result.get("booking_time")
            call.booking_date = _parse_booking_date(result.get("booking_date"))
            call.analysed_at = datetime.now(timezone.utc)
            call.status = "completed"

            if call.classification == "VOICEMAIL":
                call.call_outcome = "voicemail"

            _increment_usage(account)
            logger.info(
                "Retry %d succeeded for call %s: %s",
                call.retry_count, call.id, call.classification,
            )
        except Exception as e:
            logger.warning(
                "Retry %d failed for call %s: %s",
                call.retry_count, call.id, e,
            )
        retried += 1

    db.session.commit()
    return retried


def run_full_sync(account, days=7):
    """Run all poll functions for an account with the given lookback period.

    Returns a dict with counts: {recordings, missed, short_answered, retried}.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    logger.info(
        "Account %s: Full sync, looking back %d days (since %s)",
        account.id, days, since.isoformat(),
    )

    results = {"recordings": 0, "missed": 0, "short_answered": 0, "retried": 0}

    try:
        results["recordings"] = poll_account(account, since)
    except Exception as e:
        logger.exception("Error polling recordings for account %s: %s", account.id, e)

    try:
        results["missed"] = poll_missed_calls(account, since)
    except Exception as e:
        logger.exception("Error polling missed calls for account %s: %s", account.id, e)

    try:
        results["short_answered"] = poll_short_answered_calls(account, since)
    except Exception as e:
        logger.exception("Error polling short calls for account %s: %s", account.id, e)

    try:
        results["retried"] = retry_failed_submissions(account)
    except Exception as e:
        logger.exception("Error retrying failed submissions for account %s: %s", account.id, e)

    total = sum(results.values())
    logger.info(
        "Account %s: Sync complete — %d recordings, %d missed, %d short, %d retried (%d total)",
        account.id, results["recordings"], results["missed"],
        results["short_answered"], results["retried"], total,
    )

    return results
