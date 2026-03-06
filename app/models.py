from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Account(UserMixin, db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    twilio_account_sid = db.Column(db.String(255))
    twilio_auth_token_encrypted = db.Column(db.Text)
    twilio_service_sid = db.Column(db.String(255))
    webhook_secret = db.Column(db.String(255))
    timezone = db.Column(db.String(50), default="Australia/Adelaide")
    callrail_api_key_encrypted = db.Column(db.Text)
    callrail_account_id = db.Column(db.String(50))
    call_source = db.Column(db.String(20), default="twilio")  # "twilio" or "callrail"
    onboarding_completed = db.Column(db.Boolean, default=False, nullable=False)

    # Stripe billing
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    stripe_plan = db.Column(db.String(20), default="free")  # free/starter/pro/agency
    plan_calls_limit = db.Column(db.Integer, default=10)
    plan_calls_used = db.Column(db.Integer, default=0)
    plan_period_start = db.Column(db.DateTime)
    plan_period_end = db.Column(db.DateTime)
    subscription_status = db.Column(db.String(20), default="active")  # active/past_due/cancelled

    # Password reset
    password_reset_token = db.Column(db.String(64))
    password_reset_expires = db.Column(db.DateTime)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    tracking_lines = db.relationship("TrackingLine", backref="account", lazy=True)
    calls = db.relationship("Call", backref="account", lazy=True)
    partners = db.relationship("Partner", backref="account", lazy=True)

    @property
    def user_type(self):
        return "account"

    def get_id(self):
        return f"account:{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def at_usage_limit(self):
        """True if account has reached their plan's call processing limit."""
        if self.is_admin:
            return False
        if self.plan_calls_used is not None and self.plan_calls_limit is not None:
            return self.plan_calls_used >= self.plan_calls_limit
        return False


class Partner(db.Model):
    __tablename__ = "partners"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    cost_per_lead = db.Column(db.Numeric(10, 2), default=0)  # per booked job
    cost_per_call = db.Column(db.Numeric(10, 2), default=0)  # per answered call
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    tracking_lines = db.relationship("TrackingLine", backref="partner", lazy=True)


class TrackingLine(db.Model):
    __tablename__ = "tracking_lines"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=True)
    twilio_phone_number = db.Column(db.String(20))
    callrail_tracker_id = db.Column(db.String(50))
    callrail_tracking_number = db.Column(db.String(20))
    label = db.Column(db.String(255))
    partner_name = db.Column(db.String(255))
    partner_phone = db.Column(db.String(20))
    cost_per_lead = db.Column(db.Numeric(10, 2), default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    calls = db.relationship("Call", backref="tracking_line", lazy=True)


class Call(db.Model):
    __tablename__ = "calls"
    __table_args__ = (
        db.UniqueConstraint('account_id', 'twilio_call_sid', name='uq_call_account_call_sid'),
        db.UniqueConstraint('account_id', 'twilio_recording_sid', name='uq_call_account_recording_sid'),
        db.UniqueConstraint('account_id', 'callrail_call_id', name='uq_call_account_callrail_id'),
        db.Index('ix_call_account_date', 'account_id', 'call_date'),
        db.Index('ix_call_account_line', 'account_id', 'tracking_line_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    tracking_line_id = db.Column(
        db.Integer, db.ForeignKey("tracking_lines.id"), nullable=True
    )
    twilio_call_sid = db.Column(db.String(255))
    twilio_recording_sid = db.Column(db.String(255))
    caller_number = db.Column(db.String(20))
    call_duration = db.Column(db.Integer)
    call_date = db.Column(db.DateTime)
    recording_url = db.Column(db.Text)
    source = db.Column(db.String(20), default="twilio")
    callrail_call_id = db.Column(db.String(50))
    retry_count = db.Column(db.Integer, default=0)

    call_outcome = db.Column(db.String(20), nullable=False, default="answered")

    # Analysis results
    transcript_sid = db.Column(db.String(255))
    status = db.Column(db.String(20), default="pending")
    classification = db.Column(db.String(20))
    confidence = db.Column(db.Numeric(3, 2))
    summary = db.Column(db.Text)
    service_type = db.Column(db.String(100))
    urgent = db.Column(db.Boolean)
    full_transcript = db.Column(db.Text)
    analysed_at = db.Column(db.DateTime)

    # Customer booking details (AI-extracted)
    customer_name = db.Column(db.String(255))
    customer_address = db.Column(db.Text)
    booking_time = db.Column(db.String(255))
    booking_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    tracking_line_id = db.Column(
        db.Integer, db.ForeignKey("tracking_lines.id"), nullable=True
    )
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    total_calls = db.Column(db.Integer)
    booked_calls = db.Column(db.Integer)
    amount = db.Column(db.Numeric(10, 2))
    status = db.Column(db.String(20), default="draft")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )


shared_dashboard_lines = db.Table(
    "shared_dashboard_lines",
    db.Column(
        "shared_dashboard_id",
        db.Integer,
        db.ForeignKey("shared_dashboards.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tracking_line_id",
        db.Integer,
        db.ForeignKey("tracking_lines.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class SharedDashboard(db.Model):
    __tablename__ = "shared_dashboards"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=False)
    share_token = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True)
    show_recordings = db.Column(db.Boolean, default=True)
    show_transcripts = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    account = db.relationship("Account", backref="shared_dashboards")
    partner = db.relationship("Partner")
    tracking_lines = db.relationship(
        "TrackingLine", secondary=shared_dashboard_lines, lazy="select"
    )
