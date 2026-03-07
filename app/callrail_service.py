"""CallRail API helper functions for CallOutcome."""

import logging

import requests

logger = logging.getLogger(__name__)

CALLRAIL_API_BASE = "https://api.callrail.com"


def _get_headers(api_key):
    """Build auth headers for CallRail API requests."""
    return {"Authorization": f'Token token="{api_key}"'}


# --- Credential Validation ---


def validate_callrail_credentials(api_key):
    """Test CallRail credentials by fetching the accounts list.

    Returns True if valid, False otherwise.
    """
    url = f"{CALLRAIL_API_BASE}/v3/a.json"
    try:
        resp = requests.get(url, headers=_get_headers(api_key), timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False


# --- Account Fetch ---


def fetch_callrail_accounts(api_key):
    """Fetch all accounts accessible with this API key.

    Returns:
        List of dicts with id and name.
    """
    url = f"{CALLRAIL_API_BASE}/v3/a.json"
    resp = requests.get(url, headers=_get_headers(api_key), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    return [
        {"id": acct["id"], "name": acct["name"]}
        for acct in data.get("accounts", [])
    ]


# --- Tracker Fetch ---


def fetch_callrail_trackers(api_key, account_id):
    """Fetch all call tracking numbers (trackers) for an account.

    Handles pagination automatically.

    Returns:
        List of tracker dicts with id, name, and tracking_phone_number.
    """
    url = f"{CALLRAIL_API_BASE}/v3/a/{account_id}/trackers.json"
    headers = _get_headers(api_key)
    all_trackers = []
    page = 1

    while True:
        params = {"per_page": 100, "page": page}
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        trackers = data.get("trackers", [])
        for t in trackers:
            all_trackers.append({
                "id": t["id"],
                "name": t.get("name", ""),
                "tracking_phone_number": t.get("tracking_phone_number", ""),
            })

        # Check for more pages
        total_pages = data.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return all_trackers


# --- Call Fetch ---


def fetch_callrail_calls(api_key, account_id, date_after=None):
    """Fetch calls from CallRail with recording and transcription fields.

    Handles pagination automatically.

    Args:
        api_key: CallRail API key
        account_id: CallRail account ID
        date_after: Only fetch calls after this datetime (optional)

    Returns:
        List of call dicts from CallRail API.
    """
    url = f"{CALLRAIL_API_BASE}/v3/a/{account_id}/calls.json"
    headers = _get_headers(api_key)
    all_calls = []
    page = 1

    while True:
        params = {
            "per_page": 100,
            "page": page,
            "fields": "recording,transcription",
        }
        if date_after:
            params["date_range[start_date]"] = date_after.strftime("%Y-%m-%d")

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        all_calls.extend(data.get("calls", []))

        total_pages = data.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return all_calls


# --- Recording URL ---


def fetch_callrail_recording_url(api_key, account_id, call_id):
    """Fetch the recording URL for a specific call.

    Args:
        api_key: CallRail API key
        account_id: CallRail account ID
        call_id: CallRail call ID

    Returns:
        Recording URL string, or None if no recording exists.
    """
    url = f"{CALLRAIL_API_BASE}/v3/a/{account_id}/calls/{call_id}.json"
    resp = requests.get(url, headers=_get_headers(api_key), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    return data.get("recording")
