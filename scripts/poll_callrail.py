"""Cron job: Poll CallRail for new calls, transcribe, and classify.

Run every 5 minutes via Railway Cron Jobs:
    python scripts/poll_callrail.py

One-time backfill (e.g. recover missed calls from last 7 days):
    python scripts/poll_callrail.py --days 7
"""

import argparse
import os
import sys
import logging
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import db, Account, Call, TrackingLine
from app.callrail_service import fetch_callrail_calls
from app.ai_classifier import transcribe_recording, classify_transcript

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_booking_date(value):
    """Parse an ISO 8601 booking_date string into a datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None

# Default lookback: 24 hours (covers cron gaps, Railway restarts, etc.)
# The dedup logic prevents double-processing.
DEFAULT_LOOKBACK_HOURS = 24

# Minimum recording duration to process (seconds).
MIN_RECORDING_SECONDS = 3


def process_pending_recordings(account):
    """Process CallRail calls that have a recording but haven't been transcribed yet."""
    pending_calls = Call.query.filter_by(
        account_id=account.id,
        source="callrail",
        status="processing",
    ).all()

    if not pending_calls:
        return 0

    logger.info(
        "Account %s: %d pending CallRail recordings to process",
        account.id, len(pending_calls),
    )

    processed = 0
    for call in pending_calls:
        if not call.recording_url:
            call.status = "failed"
            logger.warning("Call %s has no recording URL, marking failed", call.id)
            continue

        # Check usage limit before transcribing (costs money)
        if account.at_usage_limit:
            call.status = "limit_reached"
            logger.info("Account %s at limit, call %s set to limit_reached", account.id, call.id)
            continue

        try:
            # Transcribe the recording
            transcript_text = transcribe_recording(call.recording_url)
            call.full_transcript = transcript_text

            # Classify the transcript
            tracking_line = call.tracking_line
            biz_name = (tracking_line.label or tracking_line.partner_name) if tracking_line else None
            results = classify_transcript(transcript_text, business_name=biz_name, call_date=call.call_date)
            call.classification = results.get("classification")
            call.confidence = results.get("confidence")
            call.summary = results.get("summary")
            call.service_type = results.get("service_type")
            call.urgent = results.get("urgent", False)
            call.customer_name = results.get("customer_name")
            call.customer_address = results.get("customer_address")
            call.booking_time = results.get("booking_time")
            call.booking_date = _parse_booking_date(results.get("booking_date"))
            call.analysed_at = datetime.now(timezone.utc)
            call.status = "completed"

            if call.classification == "VOICEMAIL":
                call.call_outcome = "voicemail"

            logger.info(
                "Call %s classified: %s", call.id, call.classification
            )
            processed += 1

        except Exception as e:
            logger.exception("Failed to process call %s: %s", call.id, e)
            call.status = "failed"

    db.session.commit()
    return processed


def backfill_callrail_calls(account, since):
    """Fetch recent calls from CallRail API and create records for any missing ones."""
    if not account.callrail_api_key_encrypted or not account.callrail_account_id:
        logger.info("Account %s: No CallRail credentials, skipping backfill", account.id)
        return 0

    logger.info(
        "Account %s: Backfilling CallRail calls since %s",
        account.id, since.isoformat(),
    )

    calls_data = fetch_callrail_calls(
        account.callrail_api_key_encrypted,
        account.callrail_account_id,
        date_after=since,
    )

    new_count = 0
    for cr_call in calls_data:
        call_id = str(cr_call.get("id", ""))
        if not call_id:
            continue

        # Dedup on callrail_call_id
        existing = Call.query.filter_by(
            account_id=account.id, callrail_call_id=call_id
        ).first()
        if existing:
            continue

        duration = int(cr_call.get("duration") or 0)
        if duration < MIN_RECORDING_SECONDS:
            continue

        answered = cr_call.get("answered", False)
        customer_phone = cr_call.get("customer_phone_number", "")
        tracking_phone = cr_call.get("tracking_phone_number", "")
        recording_url = cr_call.get("recording")
        transcription = cr_call.get("transcription")
        start_time = cr_call.get("start_time")

        # Match tracking line by CallRail tracking number
        tracking_line = TrackingLine.query.filter_by(
            account_id=account.id,
            callrail_tracking_number=tracking_phone,
            active=True,
        ).first()

        # Parse call date
        call_date = None
        if start_time:
            try:
                call_date = datetime.fromisoformat(start_time)
            except (ValueError, TypeError):
                call_date = datetime.now(timezone.utc)

        # Determine outcome and status
        if not answered or not recording_url:
            # Missed call
            call = Call(
                account_id=account.id,
                tracking_line_id=tracking_line.id if tracking_line else None,
                callrail_call_id=call_id,
                caller_number=customer_phone,
                call_duration=duration,
                call_date=call_date,
                recording_url=recording_url,
                source="callrail",
                call_outcome="missed",
                status="completed",
            )
            db.session.add(call)
            new_count += 1
            continue

        # Answered call — check limit before doing costly classification
        if account.at_usage_limit:
            call = Call(
                account_id=account.id,
                tracking_line_id=tracking_line.id if tracking_line else None,
                callrail_call_id=call_id,
                caller_number=customer_phone,
                call_duration=duration,
                call_date=call_date,
                recording_url=recording_url,
                source="callrail",
                call_outcome="answered",
                status="limit_reached",
            )
            db.session.add(call)
            new_count += 1
            continue

        call = Call(
            account_id=account.id,
            tracking_line_id=tracking_line.id if tracking_line else None,
            callrail_call_id=call_id,
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
            # Transcript available — classify immediately
            try:
                biz_name = (tracking_line.label or tracking_line.partner_name) if tracking_line else None
                results = classify_transcript(transcription, business_name=biz_name, call_date=call_date)
                call.full_transcript = transcription
                call.classification = results.get("classification")
                call.confidence = results.get("confidence")
                call.summary = results.get("summary")
                call.service_type = results.get("service_type")
                call.urgent = results.get("urgent", False)
                call.customer_name = results.get("customer_name")
                call.customer_address = results.get("customer_address")
                call.booking_time = results.get("booking_time")
                call.booking_date = _parse_booking_date(results.get("booking_date"))
                call.analysed_at = datetime.now(timezone.utc)
                call.status = "completed"

                if call.classification == "VOICEMAIL":
                    call.call_outcome = "voicemail"

                logger.info(
                    "Backfill call %s classified: %s", call_id, call.classification
                )
            except Exception as e:
                logger.exception(
                    "Failed to classify backfill call %s: %s", call_id, e
                )
                call.status = "failed"
        # else: no transcript, status stays "processing" for process_pending_recordings

        new_count += 1

    db.session.commit()
    return new_count


def retry_failed_callrail(account):
    """Re-process failed CallRail calls (up to 3 retries)."""
    if account.at_usage_limit:
        return 0

    failed_calls = Call.query.filter_by(
        account_id=account.id,
        source="callrail",
        status="failed",
    ).filter(Call.retry_count < 3).all()

    if not failed_calls:
        return 0

    retried = 0
    for call in failed_calls:
        if not call.recording_url:
            continue

        call.retry_count = (call.retry_count or 0) + 1

        try:
            # Transcribe the recording
            transcript_text = transcribe_recording(call.recording_url)
            call.full_transcript = transcript_text

            # Classify the transcript
            tracking_line = call.tracking_line
            biz_name = (tracking_line.label or tracking_line.partner_name) if tracking_line else None
            results = classify_transcript(transcript_text, business_name=biz_name, call_date=call.call_date)
            call.classification = results.get("classification")
            call.confidence = results.get("confidence")
            call.summary = results.get("summary")
            call.service_type = results.get("service_type")
            call.urgent = results.get("urgent", False)
            call.customer_name = results.get("customer_name")
            call.customer_address = results.get("customer_address")
            call.booking_time = results.get("booking_time")
            call.booking_date = _parse_booking_date(results.get("booking_date"))
            call.analysed_at = datetime.now(timezone.utc)
            call.status = "completed"

            if call.classification == "VOICEMAIL":
                call.call_outcome = "voicemail"

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


def main():
    parser = argparse.ArgumentParser(description="Poll CallRail for new calls")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Lookback period in days (for backfill). Default: 24 hours.",
    )
    args = parser.parse_args()

    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        logger.info("Backfill mode: looking back %d days", args.days)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

    app = create_app()
    with app.app_context():
        accounts = Account.query.filter(
            Account.callrail_api_key_encrypted.isnot(None)
        ).all()

        logger.info("Polling %d CallRail accounts", len(accounts))

        total_backfilled = 0
        total_processed = 0
        total_retried = 0
        for account in accounts:
            try:
                count = backfill_callrail_calls(account, since)
                total_backfilled += count
                if count:
                    logger.info("Account %s: %d new CallRail calls", account.id, count)
            except Exception as e:
                logger.exception("Error backfilling account %s: %s", account.id, e)

            try:
                processed = process_pending_recordings(account)
                total_processed += processed
                if processed:
                    logger.info("Account %s: %d recordings processed", account.id, processed)
            except Exception as e:
                logger.exception("Error processing recordings for account %s: %s", account.id, e)

            try:
                retried = retry_failed_callrail(account)
                total_retried += retried
                if retried:
                    logger.info("Account %s: retried %d failed calls", account.id, retried)
            except Exception as e:
                logger.exception("Error retrying failed calls for account %s: %s", account.id, e)

        logger.info(
            "Done. %d backfilled, %d processed, %d retried.",
            total_backfilled, total_processed, total_retried,
        )


if __name__ == "__main__":
    main()
