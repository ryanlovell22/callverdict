"""One-off script: Delete calls that have no tracking line assigned.

These are calls from Twilio numbers (e.g. hot tub lines) that aren't
configured as tracking lines in CallOutcome. Safe to delete — they
were never meant to be tracked here.

Usage:
    python scripts/cleanup_unmatched_calls_2026-03-01.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import db, Call

app = create_app()
with app.app_context():
    unmatched = Call.query.filter(Call.tracking_line_id.is_(None)).all()
    print(f"Found {len(unmatched)} calls with no tracking line:")
    for call in unmatched:
        print(f"  Call #{call.id}: {call.caller_number} — {call.summary or call.status}")

    if unmatched:
        count = Call.query.filter(Call.tracking_line_id.is_(None)).delete()
        db.session.commit()
        print(f"\nDeleted {count} unmatched calls.")
    else:
        print("\nNothing to delete.")
