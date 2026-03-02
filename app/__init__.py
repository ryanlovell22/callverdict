from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate

from .config import Config
from .models import db, Account

login_manager = LoginManager()
login_manager.login_view = "auth.login"
migrate = Migrate()


@login_manager.user_loader
def load_user(user_id):
    if ":" in str(user_id):
        prefix, uid = user_id.split(":", 1)
        uid = int(uid)
        if prefix == "account":
            return db.session.get(Account, uid)
    # Fallback for legacy sessions without prefix
    return db.session.get(Account, int(user_id))


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    import pytz

    @app.template_filter('localtime')
    def localtime_filter(value, fmt='%A, %-d %B %Y at %-I:%M %p'):
        if value is None:
            return '\u2014'
        try:
            from flask_login import current_user
            tz_name = getattr(current_user, 'timezone', None) or 'Australia/Adelaide'
            if current_user.user_type == 'partner':
                account = db.session.get(Account, current_user.account_id)
                tz_name = account.timezone if account else tz_name
            local_tz = pytz.timezone(tz_name)
        except Exception:
            local_tz = pytz.timezone('Australia/Adelaide')
        if value.tzinfo is None:
            value = pytz.utc.localize(value)
        return value.astimezone(local_tz).strftime(fmt)

    from .auth import bp as auth_bp
    from .dashboard import bp as dashboard_bp
    from .lines import bp as lines_bp
    from .webhooks import bp as webhooks_bp
    from .upload import bp as upload_bp
    from .partners import bp as partners_bp
    from .settings import bp as settings_bp
    from .landing import bp as landing_bp
    from .billing import bp as billing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(lines_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(partners_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(landing_bp)
    app.register_blueprint(billing_bp)

    from .shared import bp as shared_bp
    app.register_blueprint(shared_bp)

    @app.route('/health')
    def health_check():
        try:
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            return {'status': 'healthy'}, 200
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}, 500

    return app
