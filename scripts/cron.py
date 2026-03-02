"""Unified cron job: polls Twilio and CallRail for all accounts.

Run every 5 minutes via Railway cron service:
    python scripts/cron.py

Each poller creates its own Flask app context internally,
so this script just calls them in sequence.
"""

import sys
import os

# Ensure project root is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import db, Call

from scripts.poll_twilio import main as poll_twilio
from scripts.poll_callrail import main as poll_callrail


def migrate_short_calls():
    """One-time fix: reclassify existing short answered calls as missed.

    Safe to run multiple times — only updates rows that still match.
    Remove this function (and its call below) after it has run once.
    """
    app = create_app()
    with app.app_context():
        updated = Call.query.filter(
            Call.call_outcome == "answered",
            Call.call_duration <= 20,
        ).update(
            {
                Call.call_outcome: "missed",
                Call.classification: None,
            },
            synchronize_session=False,
        )
        db.session.commit()
        if updated:
            print(f"=== Migrated {updated} short calls from 'answered' to 'missed' ===")


if __name__ == "__main__":
    # One-time data migration — remove after first successful run
    migrate_short_calls()

    print("=== Cron start: Twilio polling ===")
    poll_twilio()

    print("=== Cron start: CallRail polling ===")
    poll_callrail()

    print("=== Cron complete ===")
