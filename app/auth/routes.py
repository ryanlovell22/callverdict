import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user

from ..models import db, Account
from ..email_service import send_email
from ..extensions import limiter
from . import bp


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute")
def login():
    if current_user.is_authenticated:
        if hasattr(current_user, 'onboarding_completed') and not current_user.onboarding_completed:
            return redirect(url_for("onboarding.wizard"))
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = Account.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get("next")
            if not next_page and hasattr(user, 'onboarding_completed') and not user.onboarding_completed:
                return redirect(url_for("onboarding.wizard"))
            return redirect(next_page or url_for("dashboard.index"))

        flash("Invalid email or password.", "error")

    return render_template("auth/login.html")


@bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("5/minute")
def signup():
    if current_user.is_authenticated:
        if hasattr(current_user, 'onboarding_completed') and not current_user.onboarding_completed:
            return redirect(url_for("onboarding.wizard"))
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/signup.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/signup.html")

        if Account.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("auth/signup.html")

        is_admin = email in current_app.config.get("ADMIN_EMAILS", [])
        account = Account(name=name, email=email, is_admin=is_admin)
        account.set_password(password)
        db.session.add(account)
        db.session.commit()

        login_user(account)
        flash("Account created successfully.", "success")
        return redirect(url_for("onboarding.wizard"))

    return render_template("auth/signup.html")


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3/minute")
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        # Always show the same message to prevent email enumeration
        flash("If an account with that email exists, we've sent a password reset link.", "success")

        account = Account.query.filter_by(email=email).first()
        if account:
            token = secrets.token_urlsafe(32)
            account.password_reset_token = token
            account.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
            db.session.commit()

            reset_url = url_for("auth.reset_password", token=token, _external=True)
            send_email(
                to=account.email,
                subject="Reset your CallOutcome password",
                html=f"""
                <h2>Password Reset</h2>
                <p>Hi {account.name},</p>
                <p>Click the link below to reset your password. This link expires in 1 hour.</p>
                <p><a href="{reset_url}" style="display:inline-block;padding:12px 24px;background:#1095c1;color:white;text-decoration:none;border-radius:6px;">Reset Password</a></p>
                <p>If you didn't request this, you can safely ignore this email.</p>
                <p>— CallOutcome</p>
                """,
            )

        return redirect(url_for("auth.forgot_password"))

    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    account = Account.query.filter_by(password_reset_token=token).first()

    if not account or not account.password_reset_expires:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("auth.forgot_password"))

    if account.password_reset_expires < datetime.now(timezone.utc):
        account.password_reset_token = None
        account.password_reset_expires = None
        db.session.commit()
        flash("This reset link has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)

        account.set_password(password)
        account.password_reset_token = None
        account.password_reset_expires = None
        db.session.commit()

        flash("Password reset successfully. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
