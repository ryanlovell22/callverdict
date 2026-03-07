from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from ..models import db, TrackingLine, Partner, Account
from ..decorators import account_required
from ..phone_utils import get_available_numbers
from . import bp


@bp.route("/")
@login_required
@account_required
def index():
    lines = TrackingLine.query.filter_by(account_id=current_user.id).order_by(
        TrackingLine.label
    ).all()
    partners = Partner.query.filter_by(account_id=current_user.id).order_by(
        Partner.name
    ).all()

    return render_template(
        "lines/index.html",
        lines=lines,
        partners=partners,
        active_page="lines",
    )


@bp.route("/add", methods=["GET", "POST"])
@login_required
@account_required
def add():
    partners = Partner.query.filter_by(account_id=current_user.id).order_by(
        Partner.name
    ).all()
    account = db.session.get(Account, current_user.id)

    if request.method == "POST":
        selected_number = request.form.get("twilio_phone_number", "").strip()

        # Look up CallRail metadata if this is a CallRail number
        callrail_tracker_id = request.form.get("callrail_tracker_id", "").strip() or None
        callrail_tracking_number = request.form.get("callrail_tracking_number", "").strip() or None

        partner_id = request.form.get("partner_id", type=int) or None
        line = TrackingLine(
            account_id=current_user.id,
            partner_id=partner_id,
            twilio_phone_number=selected_number,
            callrail_tracker_id=callrail_tracker_id,
            callrail_tracking_number=callrail_tracking_number,
            label=request.form.get("label", "").strip(),
        )
        db.session.add(line)
        db.session.commit()
        flash("Tracking line added.", "success")
        return redirect(url_for("lines.index"))

    available_numbers = get_available_numbers(account) if account else []

    return render_template(
        "lines/form.html",
        line=None,
        partners=partners,
        available_numbers=available_numbers,
        active_page="lines",
    )


@bp.route("/<int:line_id>/edit", methods=["GET", "POST"])
@login_required
@account_required
def edit(line_id):
    line = TrackingLine.query.filter_by(
        id=line_id, account_id=current_user.id
    ).first_or_404()
    partners = Partner.query.filter_by(account_id=current_user.id).order_by(
        Partner.name
    ).all()
    account = db.session.get(Account, current_user.id)

    if request.method == "POST":
        selected_number = request.form.get("twilio_phone_number", "").strip()

        line.twilio_phone_number = selected_number
        line.callrail_tracker_id = request.form.get("callrail_tracker_id", "").strip() or None
        line.callrail_tracking_number = request.form.get("callrail_tracking_number", "").strip() or None
        line.label = request.form.get("label", "").strip()
        line.partner_id = request.form.get("partner_id", type=int) or None
        line.active = "active" in request.form
        db.session.commit()
        flash("Tracking line updated.", "success")
        return redirect(url_for("lines.index"))

    available_numbers = get_available_numbers(account, exclude_line_id=line.id) if account else []

    return render_template(
        "lines/form.html",
        line=line,
        partners=partners,
        available_numbers=available_numbers,
        active_page="lines",
    )


@bp.route("/<int:line_id>/delete", methods=["POST"])
@login_required
@account_required
def delete(line_id):
    line = TrackingLine.query.filter_by(
        id=line_id, account_id=current_user.id
    ).first_or_404()
    db.session.delete(line)
    db.session.commit()
    flash("Tracking line deleted.", "success")
    return redirect(url_for("lines.index"))


@bp.route("/bulk-assign", methods=["POST"])
@login_required
@account_required
def bulk_assign():
    data = request.get_json(silent=True) or {}
    line_ids = data.get("line_ids", [])
    partner_id = data.get("partner_id")  # None to unassign

    if not line_ids:
        return jsonify({"error": "No lines selected."}), 400

    # Validate partner belongs to this account (if assigning)
    if partner_id is not None:
        partner = Partner.query.filter_by(
            id=partner_id, account_id=current_user.id
        ).first()
        if not partner:
            return jsonify({"error": "Partner not found."}), 404

    lines = TrackingLine.query.filter(
        TrackingLine.id.in_(line_ids),
        TrackingLine.account_id == current_user.id,
    ).all()

    for line in lines:
        line.partner_id = partner_id

    db.session.commit()
    return jsonify({"updated": len(lines)})
