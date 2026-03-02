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

from scripts.poll_twilio import main as poll_twilio
from scripts.poll_callrail import main as poll_callrail


if __name__ == "__main__":
    print("=== Cron start: Twilio polling ===")
    poll_twilio()

    print("=== Cron start: CallRail polling ===")
    poll_callrail()

    print("=== Cron complete ===")
