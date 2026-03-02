"""AI-powered call transcription and classification for CallVerdict.

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
    "a trades business in Australia. Your job is to determine whether "
    "the customer booked a job during this call.\n\n"
    "IMPORTANT — Speaker roles:\n"
    "- The CUSTOMER is the person who initiated the call (the caller). "
    "They are seeking a service or repair.\n"
    "- The TRADIE/BUSINESS is the person who answers the phone. They "
    "are the service provider offering to do the work.\n"
    "- Do NOT confuse the two. The tradie is NOT the customer. When "
    "extracting 'customer_name', only use the caller's name, never "
    "the business or tradie's name.\n"
    "- In summaries, refer to 'the customer' (caller) and 'the tradie' "
    "or 'the business' (answerer) clearly.\n\n"
    "Classify the call as one of:\n\n"
    "JOB_BOOKED - The customer and business agreed on a time or "
    "arrangement for work to be done. This includes: scheduling an "
    "appointment, accepting a quote, agreeing someone will come out, "
    "providing or agreeing to text their address, or any clear "
    "commitment to proceed.\n\n"
    "NOT_BOOKED - No job was booked. This includes: general enquiries "
    "without commitment, wrong numbers, price shopping "
    "without booking, spam/robocalls, or calls where the customer "
    "said they would think about it.\n\n"
    "VOICEMAIL - The customer left a voicemail message. Only one "
    "person is speaking (the customer) and there is no live "
    "conversation. The recording is a message left after a beep or "
    "automated greeting.\n\n"
    "Also extract:\n"
    "- A brief one-sentence summary of the call\n"
    "- The service type discussed (e.g. oven repair, dishwasher, "
    "washing machine, appliance repair)\n"
    "- Whether the customer mentioned urgency (same day, emergency)\n"
    "- The customer's name (the CALLER's name, not the tradie's name) "
    "if they mention it\n"
    "- The customer's address if they mention it\n"
    "- The booking time if a job was booked (e.g. 'tomorrow morning', "
    "'Wednesday 2pm', 'this afternoon')"
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


def transcribe_recording(recording_url):
    """Download a recording and transcribe it using OpenAI Whisper.

    Args:
        recording_url: URL of the audio file to transcribe.

    Returns:
        Transcript text string.
    """
    client = _get_openai_client()
    tmp_path = None

    try:
        # Download the recording to a temp file
        resp = requests.get(recording_url, timeout=60, stream=True)
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


def classify_transcript(transcript_text, business_name=None):
    """Classify a call transcript using GPT-4o-mini.

    Args:
        transcript_text: The full transcript text to classify.
        business_name: Optional business/tradie name answering the calls.
            When provided, helps the AI distinguish the tradie from the customer.

    Returns:
        Dict with classification, confidence, summary, service_type,
        urgent, customer_name, customer_address, booking_time.
    """
    client = _get_openai_client()

    user_content = ""
    if business_name:
        user_content += (
            f'The business answering these calls is "{business_name}". '
            "If you see this name in the transcript, that person is the "
            "TRADIE (the one answering), NOT the customer.\n\n"
        )
    user_content += f"Here is the call transcript:\n\n{transcript_text}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
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
        }

    except Exception:
        logger.exception("Failed to classify transcript")
        raise
