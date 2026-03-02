from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, Partner
from . import bp


def account_required(f):
    """Block partner users from accessing these routes."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.user_type != "account":
            abort(403)
        return f(*args, **kwargs)
    return decorated


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
        email = request.form.get("email", "").strip().lower()

        if not name or not email:
            flash("Name and email are required.", "error")
            return render_template("partners/form.html", active_page="partners")

        # Check for duplicate email
        if Partner.query.filter_by(email=email).first():
            flash("That email is already in use.", "error")
            return render_template("partners/form.html", active_page="partners")

        partner = Partner(
            account_id=current_user.id,
            name=name,
            email=email,
        )
        db.session.add(partner)
        db.session.commit()

        flash(f"Partner '{name}' created.", "success")
        return redirect(url_for("partners.index"))

    return render_template("partners/form.html", active_page="partners")


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
