"""Stripe billing helpers for CallOutcome."""

import logging

import stripe
from flask import current_app

logger = logging.getLogger(__name__)

PLAN_LIMITS = {
    "free": 10,
    "starter": 100,
    "pro": 500,
    "agency": 1500,
}


def _get_stripe():
    """Configure Stripe with the secret key."""
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout_session(account, price_id, success_url, cancel_url):
    """Create a Stripe Checkout session for subscription signup."""
    s = _get_stripe()

    # Create or reuse Stripe customer
    if not account.stripe_customer_id:
        customer = s.Customer.create(
            email=account.email,
            name=account.name,
            metadata={"calloutcome_account_id": str(account.id)},
        )
        account.stripe_customer_id = customer.id
        from .models import db
        db.session.commit()

    session = s.checkout.Session.create(
        customer=account.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"calloutcome_account_id": str(account.id)},
    )

    return session.url


def create_customer_portal_session(account, return_url):
    """Create a Stripe Customer Portal session for subscription management."""
    s = _get_stripe()

    if not account.stripe_customer_id:
        return None

    session = s.billing_portal.Session.create(
        customer=account.stripe_customer_id,
        return_url=return_url,
    )

    return session.url


def handle_checkout_completed(session):
    """Process a completed checkout session. Update account with Stripe IDs + plan."""
    from .models import db, Account

    account_id = session.get("metadata", {}).get("calloutcome_account_id")
    if not account_id:
        logger.warning("Checkout session missing calloutcome_account_id metadata")
        return

    account = db.session.get(Account, int(account_id))
    if not account:
        logger.warning("Account %s not found for checkout session", account_id)
        return

    account.stripe_customer_id = session.get("customer")
    account.stripe_subscription_id = session.get("subscription")

    # Determine plan from the subscription
    _update_plan_from_subscription(account, session.get("subscription"))

    db.session.commit()
    logger.info("Account %s upgraded via checkout", account_id)


def handle_subscription_updated(subscription):
    """Handle subscription plan changes."""
    from .models import db, Account

    customer_id = subscription.get("customer")
    account = Account.query.filter_by(stripe_customer_id=customer_id).first()
    if not account:
        logger.warning("No account found for Stripe customer %s", customer_id)
        return

    account.stripe_subscription_id = subscription.get("id")
    account.subscription_status = subscription.get("status", "active")

    _update_plan_from_subscription(account, subscription.get("id"))

    db.session.commit()
    logger.info("Account %s subscription updated: %s", account.id, account.stripe_plan)


def handle_subscription_deleted(subscription):
    """Handle subscription cancellation. Downgrade to free."""
    from .models import db, Account

    customer_id = subscription.get("customer")
    account = Account.query.filter_by(stripe_customer_id=customer_id).first()
    if not account:
        logger.warning("No account found for Stripe customer %s", customer_id)
        return

    account.stripe_plan = "free"
    account.plan_calls_limit = PLAN_LIMITS["free"]
    account.subscription_status = "cancelled"
    account.stripe_subscription_id = None

    db.session.commit()
    logger.info("Account %s downgraded to free (subscription cancelled)", account.id)


def handle_invoice_paid(invoice):
    """Handle paid invoice. Reset monthly usage counter."""
    from .models import db, Account

    customer_id = invoice.get("customer")
    account = Account.query.filter_by(stripe_customer_id=customer_id).first()
    if not account:
        return

    account.plan_calls_used = 0

    # Update billing period
    period_start = invoice.get("period_start")
    period_end = invoice.get("period_end")
    if period_start:
        from datetime import datetime, timezone
        account.plan_period_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
    if period_end:
        from datetime import datetime, timezone
        account.plan_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)

    db.session.commit()
    logger.info("Account %s usage reset (invoice paid)", account.id)


def _update_plan_from_subscription(account, subscription_id):
    """Look up the subscription to determine the plan level."""
    if not subscription_id:
        return

    s = _get_stripe()
    try:
        sub = s.Subscription.retrieve(subscription_id)
        price_id = sub["items"]["data"][0]["price"]["id"]

        # Map price ID to plan name
        price_map = {
            current_app.config.get("STRIPE_PRICE_STARTER"): "starter",
            current_app.config.get("STRIPE_PRICE_PRO"): "pro",
            current_app.config.get("STRIPE_PRICE_AGENCY"): "agency",
        }

        plan = price_map.get(price_id, "starter")
        account.stripe_plan = plan
        account.plan_calls_limit = PLAN_LIMITS.get(plan, 10)
        account.subscription_status = sub.get("status", "active")

    except Exception:
        logger.exception("Failed to retrieve subscription %s", subscription_id)
