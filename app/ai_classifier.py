"""AI-powered call transcription and classification for CallOutcome.

Uses OpenAI Whisper for transcription and GPT-4o-mini for classification.
Independent of Twilio Conversational Intelligence — works with any audio source.
"""

import json
import logging
import os
import tempfile

import requests
from openai import OpenAI

logger = logging.getLogger(__name__)


CLASSIFICATION_PROMPT = (
    "You are analysing a phone conversation between a customer calling "
    "a service business. Your job is to determine whether "
    "the customer booked a job during this call.\n\n"
    "IMPORTANT — Speaker roles:\n"
    "- The CUSTOMER is the person who initiated the call (the caller). "
    "They are seeking a service or repair.\n"
    "- The TECHNICIAN/BUSINESS is the person who answers the phone. They "
    "are the service provider offering to do the work.\n"
    "- Do NOT confuse the two. The technician is NOT the customer. When "
    "extracting 'customer_name', only use the caller's name, never "
    "the business or technician's name.\n"
    "- In summaries, refer to 'the customer' (caller) and 'the technician' "
    "or 'the business' (answerer) clearly.\n\n"
    "HOW TO IDENTIFY WHO IS WHO in the transcript:\n"
    "- The transcript has no speaker labels, so you must infer from context.\n"
    "- The person who ANSWERS the phone is the technician. They typically speak "
    "first with a greeting like 'Hello', '[Name] speaking', or the business "
    "name. If the transcript starts with a name (e.g. 'Greg speaking', "
    "'Dan here'), that person is the TECHNICIAN.\n"
    "- The CUSTOMER is the one who called in. They typically explain their "
    "issue, ask about services, or request a repair.\n"
    "- If both people give their name, the answerer's name is the technician "
    "and the caller's name is the customer.\n"
    "- If the only name mentioned belongs to the person who answers "
    "(the technician), then customer_name should be null — do NOT use "
    "the technician's name as the customer_name.\n\n"
    "Classify the call as one of:\n\n"
    "JOB_BOOKED - The customer and business reached a clear mutual "
    "commitment to proceed. This includes ANY of the following:\n"
    "  - Scheduling a specific appointment or time\n"
    "  - Accepting a quote or agreeing to pricing\n"
    "  - Agreeing that someone will come out (even without a specific time)\n"
    "  - The business saying they will send a booking link, form, or text "
    "and the customer agreeing to fill it out or respond\n"
    "  - Providing or agreeing to text/send their address\n"
    "  - Any clear agreement to move forward with the service, even if "
    "the exact scheduling happens via a follow-up link, text, or callback\n"
    "The key question is: did the customer commit to using this business? "
    "If yes, classify as JOB_BOOKED.\n"
    "IMPORTANT: When the call ends with the business saying they will send "
    "a booking link/form/text and the customer positively agrees (e.g. "
    "'no problem', 'sounds good', 'thank you'), that IS a booking. The "
    "customer has committed to proceed — the link is just how the details "
    "get captured. Do NOT classify these as NOT_BOOKED.\n\n"
    "NOT_BOOKED - No commitment was made. This includes: general enquiries "
    "where the customer is just asking questions without agreeing to proceed, "
    "wrong numbers, price shopping without booking, spam/robocalls, "
    "or calls where the customer said they would think about it or "
    "explicitly declined.\n\n"
    "VOICEMAIL - The customer left a voicemail message. Only one "
    "person is speaking (the customer) and there is no live "
    "conversation. The recording is a message left after a beep or "
    "automated greeting.\n\n"
    "Also extract:\n"
    "- A brief one-sentence summary of the call\n"
    "- The service type discussed (e.g. oven repair, dishwasher, "
    "washing machine, appliance repair)\n"
    "- Whether the customer mentioned urgency (same day, emergency)\n"
    "- The customer's name (the CALLER's name, not the technician's name) "
    "if they mention it\n"
    "- The customer's address if they mention it\n"
    "- The booking time if a job was booked (e.g. 'tomorrow morning', "
    "'Wednesday 2pm', 'this afternoon')\n"
    "- The resolved booking date/time in ISO 8601 format "
    "(YYYY-MM-DDTHH:MM:SS) if a job was booked and the call date is "
    "provided. Resolve relative references like 'next Tuesday' or "
    "'tomorrow' based on the call date."
)

CLASSIFICATION_SCHEMA = {
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
                "(YYYY-MM-DDTHH:MM:SS). Resolve relative references like "
                "'next Tuesday' or 'tomorrow' based on the call date provided."
            ),
        },
    },
    "required": ["classification", "summary"],
}


def _get_openai_client():
    """Create an OpenAI client using Flask config or env var."""
    try:
        from flask import current_app
        api_key = current_app.config.get("OPENAI_API_KEY")
    except RuntimeError:
        api_key = None

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    return OpenAI(api_key=api_key)


def transcribe_recording(recording_url, auth=None):
    """Download a recording and transcribe it using OpenAI Whisper.

    Args:
        recording_url: URL of the audio file to transcribe.
        auth: Optional (username, password) tuple for HTTP Basic Auth.

    Returns:
        Transcript text string.
    """
    client = _get_openai_client()
    tmp_path = None

    try:
        # Download the recording to a temp file
        resp = requests.get(recording_url, timeout=60, stream=True, auth=auth)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)

        # Send to Whisper
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        return transcript.text

    except Exception:
        logger.exception("Failed to transcribe recording from %s", recording_url)
        raise

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def classify_transcript(transcript_text, business_name=None, call_date=None, tradie_name=None, model="gpt-4o-mini"):
    """Classify a call transcript using GPT-4o-mini.

    Args:
        transcript_text: The full transcript text to classify.
        business_name: Optional business/tradie name answering the calls.
            When provided, helps the AI distinguish the tradie from the customer.
        call_date: Optional datetime of when the call took place.
            When provided, the AI resolves relative booking references
            (e.g. "next Tuesday") to actual dates.

    Returns:
        Dict with classification, confidence, summary, service_type,
        urgent, customer_name, customer_address, booking_time, booking_date.
    """
    client = _get_openai_client()

    user_content = ""
    if call_date:
        user_content += (
            f"This call took place on {call_date.strftime('%A, %-d %B %Y')}.\n\n"
        )
    if business_name:
        user_content += (
            f'The business answering these calls is "{business_name}". '
            "If you see this name in the transcript, that person is the "
            "TECHNICIAN (the one answering), NOT the customer.\n\n"
        )
    if tradie_name:
        user_content += (
            f'The technician/person answering the phone is "{tradie_name}". '
            "This is NOT the customer. Do NOT use this name as the "
            "customer_name.\n\n"
        )
    user_content += f"Here is the call transcript:\n\n{transcript_text}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{CLASSIFICATION_PROMPT}\n\n"
                        "Respond with a JSON object matching this schema:\n"
                        f"{json.dumps(CLASSIFICATION_SCHEMA, indent=2)}"
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        result_text = response.choices[0].message.content
        result = json.loads(result_text)

        return {
            "classification": result.get("classification"),
            "confidence": result.get("confidence"),
            "summary": result.get("summary"),
            "service_type": result.get("service_type"),
            "urgent": result.get("urgent", False),
            "customer_name": result.get("customer_name"),
            "customer_address": result.get("customer_address"),
            "booking_time": result.get("booking_time"),
            "booking_date": result.get("booking_date"),
        }

    except Exception:
        logger.exception("Failed to classify transcript")
        raise
