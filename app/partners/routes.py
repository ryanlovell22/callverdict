from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from ..models import db, Partner
from ..decorators import account_required
from . import bp


@bp.route("/")
@login_required
@account_required
def index():
    partners = Partner.query.filter_by(account_id=current_user.id).order_by(
        Partner.name
    ).all()
    return render_template("partners/index.html", partners=partners, active_page="partners")


@bp.route("/add", methods=["GET", "POST"])
@login_required
@account_required
def add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cost_per_lead = request.form.get("cost_per_lead") or 0
        cost_per_call = request.form.get("cost_per_call") or 0
        cost_per_voicemail = request.form.get("cost_per_voicemail") or 0
        weekly_minimum_fee = request.form.get("weekly_minimum_fee") or 0
        weekly_minimum_calls = request.form.get("weekly_minimum_calls") or 0
        qualified_call_seconds = request.form.get("qualified_call_seconds") or 60
        cost_per_qualified_call = request.form.get("cost_per_qualified_call") or 0

        if not name:
            flash("Name is required.", "error")
            return render_template("partners/form.html", partner=None, active_page="partners")

        partner = Partner(
            account_id=current_user.id,
            name=name,
            cost_per_lead=cost_per_lead,
            cost_per_call=cost_per_call,
            cost_per_voicemail=cost_per_voicemail,
            weekly_minimum_fee=weekly_minimum_fee,
            weekly_minimum_calls=weekly_minimum_calls,
            qualified_call_seconds=qualified_call_seconds,
            cost_per_qualified_call=cost_per_qualified_call,
        )
        db.session.add(partner)
        db.session.commit()

        flash(f"Partner '{name}' created.", "success")
        return redirect(url_for("partners.index"))

    return render_template("partners/form.html", partner=None, active_page="partners")


@bp.route("/<int:partner_id>/edit", methods=["GET", "POST"])
@login_required
@account_required
def edit(partner_id):
    partner = Partner.query.filter_by(
        id=partner_id, account_id=current_user.id
    ).first_or_404()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cost_per_lead = request.form.get("cost_per_lead") or 0
        cost_per_call = request.form.get("cost_per_call") or 0
        cost_per_voicemail = request.form.get("cost_per_voicemail") or 0
        weekly_minimum_fee = request.form.get("weekly_minimum_fee") or 0
        weekly_minimum_calls = request.form.get("weekly_minimum_calls") or 0
        qualified_call_seconds = request.form.get("qualified_call_seconds") or 60
        cost_per_qualified_call = request.form.get("cost_per_qualified_call") or 0

        if not name:
            flash("Name is required.", "error")
            return render_template("partners/form.html", partner=partner, active_page="partners")

        partner.name = name
        partner.cost_per_lead = cost_per_lead
        partner.cost_per_call = cost_per_call
        partner.cost_per_voicemail = cost_per_voicemail
        partner.weekly_minimum_fee = weekly_minimum_fee
        partner.weekly_minimum_calls = weekly_minimum_calls
        partner.qualified_call_seconds = qualified_call_seconds
        partner.cost_per_qualified_call = cost_per_qualified_call
        db.session.commit()

        flash(f"Partner '{name}' updated.", "success")
        return redirect(url_for("partners.index"))

    return render_template("partners/form.html", partner=partner, active_page="partners")


@bp.route("/<int:partner_id>/delete", methods=["POST"])
@login_required
@account_required
def delete(partner_id):
    partner = Partner.query.filter_by(
        id=partner_id, account_id=current_user.id
    ).first_or_404()

    # Unassign tracking lines before deleting
    for line in partner.tracking_lines:
        line.partner_id = None

    db.session.delete(partner)
    db.session.commit()
    flash("Partner deleted.", "success")
    return redirect(url_for("partners.index"))
