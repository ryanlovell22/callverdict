"""Twilio API helper functions for CallOutcome."""

import json
import logging

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
TWILIO_CI_BASE = "https://intelligence.twilio.com/v2"


def get_auth(account_sid, auth_token):
    return HTTPBasicAuth(account_sid, auth_token)


# --- Credential Validation ---


def validate_twilio_credentials(account_sid, auth_token):
    """Test Twilio credentials by fetching the account info.

    Returns True if valid, False otherwise.
    """
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}.json"
    try:
        resp = requests.get(
            url, auth=get_auth(account_sid, auth_token), timeout=10
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


# --- Phone Number Fetch ---


def fetch_twilio_phone_numbers(account_sid, auth_token):
    """Fetch all incoming phone numbers from the Twilio account.

    Returns:
        List of dicts with phone_number and friendly_name.
    """
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    params = {"PageSize": 100}
    auth = get_auth(account_sid, auth_token)
    all_numbers = []

    while url:
        resp = requests.get(url, params=params, auth=auth, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for num in data.get("incoming_phone_numbers", []):
            all_numbers.append({
                "phone_number": num["phone_number"],
                "friendly_name": num.get("friendly_name", num["phone_number"]),
            })

        next_page = data.get("next_page_uri")
        if next_page:
            url = f"https://api.twilio.com{next_page}"
            params = {}
        else:
            url = None

    return all_numbers


# --- Recording Fetch ---


def fetch_recordings(account_sid, auth_token, date_after=None):
    """Fetch call recordings from Twilio.

    Args:
        account_sid: Twilio Account SID
        auth_token: Twilio Auth Token
        date_after: Only fetch recordings created after this datetime

    Returns:
        List of recording dicts from Twilio API
    """
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Recordings.json"
    params = {"PageSize": 100}
    if date_after:
        params["DateCreated>"] = date_after.strftime("%Y-%m-%dT%H:%M:%SZ")

    auth = get_auth(account_sid, auth_token)
    all_recordings = []

    while url:
        resp = requests.get(url, params=params, auth=auth, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_recordings.extend(data.get("recordings", []))

        # Pagination
        next_page = data.get("next_page_uri")
        if next_page:
            url = f"https://api.twilio.com{next_page}"
            params = {}  # next_page_uri includes params
        else:
            url = None

    return all_recordings


def get_call_details(account_sid, auth_token, call_sid):
    """Get details about a specific call."""
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls/{call_sid}.json"
    resp = requests.get(url, auth=get_auth(account_sid, auth_token), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_calls(account_sid, auth_token, status_list, date_after=None):
    """Fetch calls from Twilio filtered by status.

    Args:
        status_list: List of call statuses to fetch (e.g. ['no-answer', 'busy', 'canceled'])
        date_after: Only fetch calls created after this datetime

    Returns:
        List of call dicts from Twilio API
    """
    auth = get_auth(account_sid, auth_token)
    all_calls = []

    for status in status_list:
        url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Calls.json"
        params = {"PageSize": 100, "Status": status}
        if date_after:
            params["StartTime>"] = date_after.strftime("%Y-%m-%dT%H:%M:%SZ")

        while url:
            resp = requests.get(url, params=params, auth=auth, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_calls.extend(data.get("calls", []))

            next_page = data.get("next_page_uri")
            if next_page:
                url = f"https://api.twilio.com{next_page}"
                params = {}
            else:
                url = None

    return all_calls


# --- Conversational Intelligence ---


def create_ci_service(account_sid, auth_token, webhook_url=None):
    """Create or reuse a Conversational Intelligence service.

    Returns:
        service_sid (str)
    """
    auth = get_auth(account_sid, auth_token)

    # Check for existing service first
    list_resp = requests.get(f"{TWILIO_CI_BASE}/Services", auth=auth, timeout=30)
    if list_resp.status_code == 200:
        for svc in list_resp.json().get("services", []):
            if svc.get("unique_name") == "calloutcome":
                logger.info("Reusing existing CI service %s", svc["sid"])
                return svc["sid"]

    # No existing service — create one
    data = {
        "UniqueName": "calloutcome",
        "FriendlyName": "CallOutcome Job Classifier",
        "AutoTranscribe": "false",
        "LanguageCode": "en-AU",
    }
    if webhook_url:
        data["WebhookUrl"] = webhook_url
        data["WebhookHttpMethod"] = "POST"

    resp = requests.post(f"{TWILIO_CI_BASE}/Services", auth=auth, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["sid"]


def create_ci_operator(account_sid, auth_token, service_sid):
    """Create the 'Job Booked?' custom operator and attach it to the service.

    Returns:
        operator_sid (str)
    """
    auth = get_auth(account_sid, auth_token)

    # Create custom operator (Twilio CI uses form-encoded, not JSON body)
    operator_url = f"{TWILIO_CI_BASE}/Operators/Custom"
    from .ai_classifier import CLASSIFICATION_PROMPT
    config = json.dumps({
        "prompt": CLASSIFICATION_PROMPT,
        "json_result_schema": {
            "type": "object",
            "properties": {
                "classification": {
                    "type": "string",
                    "enum": ["JOB_BOOKED", "NOT_BOOKED", "VOICEMAIL"],
                },
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "service_type": {"type": "string"},
                "urgent": {"type": "boolean"},
                "customer_name": {"type": "string"},
                "customer_address": {"type": "string"},
                "booking_time": {"type": "string"},
                "booking_date": {
                    "type": "string",
                    "description": (
                        "The resolved booking date/time in ISO 8601 format "
                        "(YYYY-MM-DDTHH:MM:SS). Resolve relative references "
                        "like 'next Tuesday' or 'tomorrow' based on the call date."
                    ),
                },
            },
            "required": ["classification", "summary"],
        },
    })

    resp = requests.post(
        operator_url,
        auth=auth,
        data={
            "FriendlyName": "Job Booked Classifier",
            "OperatorType": "GenerativeJSON",
            "Config": config,
        },
        timeout=30,
    )
    resp.raise_for_status()
    operator_sid = resp.json()["sid"]

    # Attach operator to service
    attach_url = (
        f"{TWILIO_CI_BASE}/Services/{service_sid}/Operators/{operator_sid}"
    )
    resp = requests.post(attach_url, auth=auth, data={}, timeout=30)
    resp.raise_for_status()

    return operator_sid


def update_ci_operator(account_sid, auth_token, operator_sid, config,
                       friendly_name="Job Booked Classifier"):
    """Update an existing custom operator's configuration.

    Args:
        operator_sid: The SID of the operator to update
        config: Dict with prompt and json_result_schema
        friendly_name: Display name for the operator

    Returns:
        Updated operator dict from Twilio API
    """
    url = f"{TWILIO_CI_BASE}/Operators/Custom/{operator_sid}"
    auth = get_auth(account_sid, auth_token)

    resp = requests.post(
        url,
        auth=auth,
        data={
            "FriendlyName": friendly_name,
            "OperatorType": "GenerativeJSON",
            "Config": json.dumps(config),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def submit_recording_to_ci(account_sid, auth_token, service_sid, recording_url):
    """Submit a Twilio recording to Conversational Intelligence for analysis.

    Args:
        recording_url: URL of the Twilio recording

    Returns:
        transcript_sid (str)
    """
    url = f"{TWILIO_CI_BASE}/Transcripts"
    auth = get_auth(account_sid, auth_token)

    channel = json.dumps({
        "media_properties": {
            "source_sid": recording_url.split("/")[-1]
            if "Recordings/" in recording_url
            else None,
            "media_url": recording_url,
        }
    })

    resp = requests.post(
        url,
        auth=auth,
        data={"ServiceSid": service_sid, "Channel": channel},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["sid"]


def submit_media_to_ci(account_sid, auth_token, service_sid, media_url):
    """Submit an external media URL to Conversational Intelligence.

    Returns:
        transcript_sid (str)
    """
    url = f"{TWILIO_CI_BASE}/Transcripts"
    auth = get_auth(account_sid, auth_token)

    channel = json.dumps({
        "media_properties": {"media_url": media_url}
    })

    resp = requests.post(
        url,
        auth=auth,
        data={"ServiceSid": service_sid, "Channel": channel},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["sid"]


def fetch_operator_results(account_sid, auth_token, transcript_sid):
    """Fetch operator results for a completed transcript.

    Returns:
        dict with classification, confidence, summary, service_type, urgent
    """
    url = f"{TWILIO_CI_BASE}/Transcripts/{transcript_sid}/OperatorResults"
    auth = get_auth(account_sid, auth_token)

    resp = requests.get(url, auth=auth, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    results = data.get("operator_results", [])
    if not results:
        return None

    # Get the first (and likely only) operator result
    result = results[0]

    # GenerativeJSON operators return results in json_results,
    # while other types use extract_results
    extracted = result.get("json_results") or result.get("extract_results") or {}

    if isinstance(extracted, str):
        try:
            extracted = json.loads(extracted)
        except json.JSONDecodeError:
            extracted = {}

    return {
        "classification": extracted.get("classification"),
        "confidence": extracted.get("confidence"),
        "summary": extracted.get("summary"),
        "service_type": extracted.get("service_type"),
        "urgent": extracted.get("urgent", False),
        "customer_name": extracted.get("customer_name"),
        "customer_address": extracted.get("customer_address"),
        "booking_time": extracted.get("booking_time"),
        "booking_date": extracted.get("booking_date"),
    }


def fetch_transcript_text(account_sid, auth_token, transcript_sid):
    """Fetch the full transcript text.

    Returns:
        str: Full transcript text with speaker labels
    """
    url = f"{TWILIO_CI_BASE}/Transcripts/{transcript_sid}/Sentences"
    auth = get_auth(account_sid, auth_token)

    all_sentences = []
    while url:
        resp = requests.get(url, auth=auth, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_sentences.extend(data.get("sentences", []))

        next_page = data.get("meta", {}).get("next_page_url")
        url = next_page if next_page else None

    lines = []
    for s in all_sentences:
        # media_channel is an integer: 1 = caller, 2 = business
        channel = s.get("media_channel", 0)
        speaker = "Customer" if channel == 1 else "Business"
        text = s.get("transcript", "")
        lines.append(f"{speaker}: {text}")

    return "\n".join(lines)
