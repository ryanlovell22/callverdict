import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytz
import requests as http_requests
from flask import render_template, request, redirect, url_for, flash, jsonify, Response, abort
from flask_login import login_required, current_user

from ..models import db, Call, TrackingLine, Account, Partner
from . import bp


def _get_user_tz():
    """Get the current user's timezone as a pytz timezone object."""
    try:
        if current_user.user_type == 'partner':
            account = db.session.get(Account, current_user.account_id)
            tz_name = account.timezone if account else 'Australia/Adelaide'
        else:
            tz_name = current_user.timezone or 'Australia/Adelaide'
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone('Australia/Adelaide')


def _local_date_to_utc(date_str, local_tz, end_of_day=False):
    """Convert a YYYY-MM-DD date string to a naive UTC datetime.

    If end_of_day=True, returns the start of the NEXT day in UTC
    (i.e., the exclusive upper bound for that date).
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt += timedelta(days=1)
    local_dt = local_tz.localize(dt)
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


@bp.route("/")
@login_required
def index():
    if current_user.user_type == "account" and not current_user.onboarding_completed:
        return redirect(url_for("onboarding.wizard"))

    from sqlalchemy import func

    # Filters
    page = request.args.get("page", 1, type=int)
    line_id = request.args.get("line", type=int)
    partner_id = request.args.get("partner", type=int)
    classification = request.args.get("classification")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    # Default to current week (Monday through today) in the user's local timezone
    local_tz = _get_user_tz()
    now_local = datetime.now(timezone.utc).astimezone(local_tz)
    today_local = now_local.date()
    monday = today_local - timedelta(days=today_local.weekday())  # weekday() 0=Mon

    if not date_from:
        date_from = monday.strftime("%Y-%m-%d")
    if not date_to:
        date_to = today_local.strftime("%Y-%m-%d")

    # Partners see only their assigned lines; accounts see everything
    if current_user.user_type == "partner":
        account_id = current_user.account_id
        partner_line_ids = [l.id for l in current_user.tracking_lines]
        query = Call.query.filter(
            Call.account_id == account_id,
            Call.tracking_line_id.in_(partner_line_ids)
        )
    else:
        account_id = current_user.id
        query = Call.query.filter_by(account_id=account_id)

    # Apply user filters to the base query
    if partner_id:
        partner_line_ids_filter = [
            l.id for l in TrackingLine.query.filter_by(
                account_id=account_id, partner_id=partner_id, active=True
            ).all()
        ]
        query = query.filter(Call.tracking_line_id.in_(partner_line_ids_filter))
    if line_id:
        query = query.filter_by(tracking_line_id=line_id)
    if classification and classification in ("JOB_BOOKED", "NOT_BOOKED"):
        query = query.filter_by(classification=classification)
    try:
        dt_from = _local_date_to_utc(date_from, local_tz)
        query = query.filter(Call.call_date >= dt_from)
    except ValueError:
        pass
    try:
        dt_to = _local_date_to_utc(date_to, local_tz, end_of_day=True)
        query = query.filter(Call.call_date < dt_to)
    except ValueError:
        pass

    # Stats via DB aggregates (on the full filtered query, including missed)
    from sqlalchemy import or_
    total = query.count()
    booked = query.filter(Call.classification == "JOB_BOOKED").count()
    not_booked = query.filter(Call.classification == "NOT_BOOKED").count()
    missed = query.filter(
        or_(
            Call.call_outcome.in_(["missed", "voicemail"]),
            Call.classification == "VOICEMAIL",
        )
    ).count()
    pending = query.filter(Call.status.in_(["pending", "processing"])).count()
    # Conversion rate: booked out of answered+analysed calls (excludes missed/pending)
    answered = booked + not_booked
    rate = round(booked / answered * 100, 1) if answered > 0 else 0

    # Lead value: per-booking + per-call + per-voicemail + per-qualified-call
    booking_value = db.session.query(
        func.coalesce(func.sum(Partner.cost_per_lead), 0)
    ).join(TrackingLine, TrackingLine.partner_id == Partner.id
    ).join(Call, Call.tracking_line_id == TrackingLine.id).filter(
        Call.id.in_(query.filter(Call.classification == "JOB_BOOKED").with_entities(Call.id))
    ).scalar()

    call_value = db.session.query(
        func.coalesce(func.sum(Partner.cost_per_call), 0)
    ).join(TrackingLine, TrackingLine.partner_id == Partner.id
    ).join(Call, Call.tracking_line_id == TrackingLine.id).filter(
        Call.id.in_(query.filter(
            Call.call_outcome == "answered",
            Call.status == "completed",
        ).with_entities(Call.id))
    ).scalar()

    voicemail_value = db.session.query(
        func.coalesce(func.sum(Partner.cost_per_voicemail), 0)
    ).join(TrackingLine, TrackingLine.partner_id == Partner.id
    ).join(Call, Call.tracking_line_id == TrackingLine.id).filter(
        Call.id.in_(query.filter(Call.classification == "VOICEMAIL").with_entities(Call.id))
    ).scalar()

    qualified_value = db.session.query(
        func.coalesce(func.sum(Partner.cost_per_qualified_call), 0)
    ).join(TrackingLine, TrackingLine.partner_id == Partner.id
    ).join(Call, Call.tracking_line_id == TrackingLine.id).filter(
        Call.id.in_(query.filter(
            Call.call_outcome == "answered",
            Call.status == "completed",
        ).with_entities(Call.id)),
        Call.call_duration >= Partner.qualified_call_seconds,
    ).scalar()

    total_value = booking_value + call_value + voicemail_value + qualified_value

    # Paginate the table query (all calls including missed)
    pagination = query.order_by(Call.call_date.desc()).paginate(page=page, per_page=50, error_out=False)
    calls = pagination.items

    if current_user.user_type == "partner":
        lines = [l for l in current_user.tracking_lines if l.active]
        partners = []
    else:
        lines = TrackingLine.query.filter_by(
            account_id=current_user.id, active=True
        ).all()
        partners = Partner.query.filter_by(account_id=current_user.id).all()

    # Build date range label
    is_default_week = (
        date_from == monday.strftime("%Y-%m-%d")
        and date_to == today_local.strftime("%Y-%m-%d")
    )
    if is_default_week:
        if monday == today_local:
            week_label = "Today: {}".format(today_local.strftime("%-d %b %Y"))
        else:
            week_label = "This week: {} - {}".format(
                monday.strftime("%-d %b"), today_local.strftime("%-d %b %Y")
            )
    else:
        week_label = "{} - {}".format(date_from, date_to)

    importing = request.args.get("importing")

    filters = {
        "line": line_id,
        "partner": partner_id,
        "classification": classification,
        "date_from": date_from or "",
        "date_to": date_to or "",
    }

    return render_template(
        "dashboard/index.html",
        calls=calls,
        lines=lines,
        partners=partners,
        week_label=week_label,
        pagination=pagination,
        importing=importing,
        stats={
            "total": total,
            "booked": booked,
            "not_booked": not_booked,
            "pending": pending,
            "rate": rate,
            "total_value": total_value,
            "missed": missed,
        },
        filters=filters,
        active_page="dashboard",
    )


@bp.route("/calls/<int:call_id>")
@login_required
def call_detail(call_id):
    if current_user.user_type == "partner":
        partner_line_ids = [l.id for l in current_user.tracking_lines]
        call = Call.query.filter(
            Call.id == call_id,
            Call.account_id == current_user.account_id,
            Call.tracking_line_id.in_(partner_line_ids)
        ).first_or_404()
    else:
        call = Call.query.filter_by(
            id=call_id, account_id=current_user.id
        ).first_or_404()
    return render_template("dashboard/call_detail.html", call=call, active_page="dashboard")


@bp.route("/calls/<int:call_id>/override", methods=["POST"])
@login_required
def override_classification(call_id):
    # Partners cannot override classifications
    if current_user.user_type == "partner":
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("dashboard.index"))

    call = Call.query.filter_by(
        id=call_id, account_id=current_user.id
    ).first_or_404()

    new_classification = request.form.get("classification")
    if new_classification in ("JOB_BOOKED", "NOT_BOOKED"):
        call.classification = new_classification
        db.session.commit()
        flash("Classification updated.", "success")

    return redirect(url_for("dashboard.call_detail", call_id=call.id))


@bp.route("/calls/<int:call_id>/recording")
@login_required
def call_recording(call_id):
    """Proxy the Twilio recording so users don't need Twilio credentials."""
    if current_user.user_type == "partner":
        partner_line_ids = [l.id for l in current_user.tracking_lines]
        call = Call.query.filter(
            Call.id == call_id,
            Call.account_id == current_user.account_id,
            Call.tracking_line_id.in_(partner_line_ids)
        ).first_or_404()
        account = db.session.get(Account, current_user.account_id)
    else:
        call = Call.query.filter_by(
            id=call_id, account_id=current_user.id
        ).first_or_404()
        account = db.session.get(Account, current_user.id)

    if not call.recording_url or not account:
        return "Recording not available", 404

    # Twilio recordings need auth; CallRail CDN URLs are pre-signed
    is_twilio = "twilio.com" in call.recording_url
    if is_twilio:
        resp = http_requests.get(
            f"{call.recording_url}.mp3",
            auth=(account.twilio_account_sid, account.twilio_auth_token_encrypted),
            stream=True,
            timeout=30,
        )
    else:
        resp = http_requests.get(
            call.recording_url,
            stream=True,
            timeout=30,
        )

    if resp.status_code != 200:
        return "Recording not available", 404

    content_type = resp.headers.get("Content-Type", "audio/mpeg")

    return Response(
        resp.iter_content(chunk_size=8192),
        content_type=content_type,
        headers={"Content-Disposition": "inline"},
    )


@bp.route("/calls/<int:call_id>/compare-openai", methods=["POST"])
@login_required
def compare_openai(call_id):
    """Admin only: re-classify with GPT-4o and compare against current GPT-4o-mini result."""
    if not getattr(current_user, 'is_admin', False):
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("dashboard.index"))

    call = Call.query.filter_by(
        id=call_id, account_id=current_user.id
    ).first_or_404()

    if not call.full_transcript:
        flash("No transcript available for this call.", "error")
        return redirect(url_for("dashboard.call_detail", call_id=call.id))

    try:
        from ..ai_classifier import classify_transcript

        business_name = None
        tradie = None
        if call.tracking_line:
            business_name = call.tracking_line.label
            tradie = (call.tracking_line.partner.name if call.tracking_line.partner else None) or call.tracking_line.partner_name

        gpt4o_result = classify_transcript(
            call.full_transcript,
            business_name=business_name,
            call_date=call.call_date,
            tradie_name=tradie,
            model="gpt-4o",
        )

        match = (
            call.classification
            and gpt4o_result.get("classification")
            and call.classification == gpt4o_result["classification"]
        )

        return render_template(
            "dashboard/compare_openai.html",
            call=call,
            openai_transcript=call.full_transcript,
            openai_result=gpt4o_result,
            match=match,
            active_page="dashboard",
        )

    except Exception as e:
        flash(f"GPT-4o analysis failed: {e}", "error")
        return redirect(url_for("dashboard.call_detail", call_id=call.id))


@bp.route("/export")
@login_required
def export_csv():
    """Export filtered calls as CSV."""
    line_id = request.args.get("line", type=int)
    partner_id = request.args.get("partner", type=int)
    classification = request.args.get("classification")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    # Default to current week (Monday through today) in the user's local timezone
    local_tz = _get_user_tz()
    now_local = datetime.now(timezone.utc).astimezone(local_tz)
    today_local = now_local.date()
    monday = today_local - timedelta(days=today_local.weekday())
    if not date_from:
        date_from = monday.strftime("%Y-%m-%d")
    if not date_to:
        date_to = today_local.strftime("%Y-%m-%d")

    # Build query (same logic as index)
    if current_user.user_type == "partner":
        account_id = current_user.account_id
        partner_line_ids = [l.id for l in current_user.tracking_lines]
        query = Call.query.filter(
            Call.account_id == account_id,
            Call.tracking_line_id.in_(partner_line_ids)
        )
    else:
        account_id = current_user.id
        query = Call.query.filter_by(account_id=account_id)

    if partner_id:
        partner_line_ids_filter = [
            l.id for l in TrackingLine.query.filter_by(
                account_id=account_id, partner_id=partner_id, active=True
            ).all()
        ]
        query = query.filter(Call.tracking_line_id.in_(partner_line_ids_filter))
    if line_id:
        query = query.filter_by(tracking_line_id=line_id)
    if classification and classification in ("JOB_BOOKED", "NOT_BOOKED"):
        query = query.filter_by(classification=classification)
    try:
        dt_from = _local_date_to_utc(date_from, local_tz)
        query = query.filter(Call.call_date >= dt_from)
    except ValueError:
        pass
    try:
        dt_to = _local_date_to_utc(date_to, local_tz, end_of_day=True)
        query = query.filter(Call.call_date < dt_to)
    except ValueError:
        pass

    calls = query.order_by(Call.call_date.desc()).all()

    # Resolve the user's timezone for date formatting
    import pytz
    try:
        if current_user.user_type == "partner":
            account = db.session.get(Account, current_user.account_id)
            tz_name = account.timezone if account else "Australia/Adelaide"
        else:
            tz_name = current_user.timezone or "Australia/Adelaide"
        local_tz = pytz.timezone(tz_name)
    except Exception:
        local_tz = pytz.timezone("Australia/Adelaide")

    def _local_date(dt):
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(local_tz).strftime("%-d %b %Y %-I:%M %p")

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Line", "Partner", "Caller", "Customer", "Duration", "Classification", "Booking Time", "Summary"])
    for call in calls:
        writer.writerow([
            _local_date(call.call_date),
            call.tracking_line.label if call.tracking_line else '',
            call.tracking_line.partner.name if call.tracking_line and call.tracking_line.partner else '',
            call.caller_number or '',
            call.customer_name or '',
            f"{call.call_duration // 60}:{call.call_duration % 60:02d}" if call.call_duration else '',
            'Missed' if call.call_outcome == 'missed' else (call.classification or call.status),
            call.booking_time or '',
            call.summary or '',
        ])

    csv_data = output.getvalue()
    # UTF-8 BOM so Excel recognises the encoding correctly
    bom = '\ufeff'
    return Response(
        bom + csv_data,
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename="calloutcome_export.csv"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )


@bp.route("/shared-links")
@login_required
def shared_links():
    if current_user.user_type != "account":
        abort(403)
    from ..models import SharedDashboard
    dashboards = SharedDashboard.query.filter_by(
        account_id=current_user.id
    ).order_by(SharedDashboard.created_at.desc()).all()

    partners = Partner.query.filter_by(account_id=current_user.id).all()
    lines = TrackingLine.query.filter_by(
        account_id=current_user.id, active=True
    ).all()

    # Group lines by partner for the checkbox fieldsets
    lines_by_partner = {}
    for line in lines:
        if line.partner_id:
            lines_by_partner.setdefault(line.partner_id, []).append(line)

    return render_template(
        "dashboard/shared_links.html",
        dashboards=dashboards,
        partners=partners,
        lines_by_partner=lines_by_partner,
        active_page="shared_links",
    )


@bp.route("/shared-links/create", methods=["POST"])
@login_required
def create_shared_link():
    if current_user.user_type != "account":
        abort(403)

    import secrets
    from werkzeug.security import generate_password_hash
    from ..models import SharedDashboard

    partner_id = request.form.get("partner_id", type=int)
    if not partner_id:
        flash("Please select a partner.", "error")
        return redirect(url_for("dashboard.shared_links"))

    # Validate partner belongs to this account
    partner = Partner.query.filter_by(id=partner_id, account_id=current_user.id).first()
    if not partner:
        flash("Invalid partner.", "error")
        return redirect(url_for("dashboard.shared_links"))

    password = request.form.get("password", "").strip()
    show_recordings = "show_recordings" in request.form
    show_transcripts = "show_transcripts" in request.form
    date_window_days = request.form.get("date_window_days", 30, type=int)
    if date_window_days not in (0, 7, 14, 30, 60, 90):
        date_window_days = 30

    # Get selected line IDs and validate they belong to this account + partner
    line_ids = request.form.getlist("line_ids", type=int)
    valid_lines = TrackingLine.query.filter(
        TrackingLine.id.in_(line_ids),
        TrackingLine.account_id == current_user.id,
        TrackingLine.partner_id == partner_id,
        TrackingLine.active == True,
    ).all() if line_ids else []

    # If no lines selected, default to all active partner lines
    if not valid_lines:
        valid_lines = TrackingLine.query.filter_by(
            account_id=current_user.id, partner_id=partner_id, active=True
        ).all()

    dashboard = SharedDashboard(
        account_id=current_user.id,
        partner_id=partner_id,
        share_token=secrets.token_urlsafe(32),
        password_hash=generate_password_hash(password) if password else None,
        show_recordings=show_recordings,
        show_transcripts=show_transcripts,
        date_window_days=date_window_days,
    )
    dashboard.tracking_lines = valid_lines
    db.session.add(dashboard)
    db.session.commit()

    flash("Dashboard Share Link created.", "success")
    return redirect(url_for("dashboard.shared_links"))


@bp.route("/shared-links/<int:dashboard_id>/toggle", methods=["POST"])
@login_required
def toggle_shared_link(dashboard_id):
    if current_user.user_type != "account":
        abort(403)
    from ..models import SharedDashboard
    dashboard = SharedDashboard.query.filter_by(
        id=dashboard_id, account_id=current_user.id
    ).first_or_404()
    dashboard.active = not dashboard.active
    db.session.commit()
    status = "enabled" if dashboard.active else "disabled"
    flash(f"Dashboard Share Link {status}.", "success")
    return redirect(url_for("dashboard.shared_links"))


@bp.route("/shared-links/<int:dashboard_id>/delete", methods=["POST"])
@login_required
def delete_shared_link(dashboard_id):
    if current_user.user_type != "account":
        abort(403)
    from ..models import SharedDashboard
    dashboard = SharedDashboard.query.filter_by(
        id=dashboard_id, account_id=current_user.id
    ).first_or_404()
    db.session.delete(dashboard)
    db.session.commit()
    flash("Dashboard Share Link deleted.", "success")
    return redirect(url_for("dashboard.shared_links"))
