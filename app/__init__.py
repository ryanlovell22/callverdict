import os
from datetime import datetime, timezone

from flask import Flask, redirect, render_template, Response
from flask_login import LoginManager
from flask_migrate import Migrate

from .config import Config
from .models import db, Account
from .extensions import limiter

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
    limiter.init_app(app)

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
    from .onboarding import bp as onboarding_bp
    from .blog import bp as blog_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(lines_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(partners_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(landing_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(blog_bp)

    from .shared import bp as shared_bp
    app.register_blueprint(shared_bp)

    @app.route('/')
    def index():
        return redirect('/welcome')

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    @app.route('/privacy')
    def privacy():
        return render_template('legal/privacy.html')

    @app.route('/terms')
    def terms():
        return render_template('legal/terms.html')

    @app.route('/health')
    def health_check():
        try:
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            return {'status': 'healthy'}, 200
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}, 500

    @app.route('/robots.txt')
    def robots_txt():
        body = (
            "User-agent: *\n"
            "Allow: /welcome\n"
            "Allow: /blog/\n"
            "Disallow: /dashboard\n"
            "Disallow: /auth/\n"
            "Disallow: /onboarding\n"
            "Disallow: /settings\n"
            "Disallow: /lines\n"
            "Disallow: /partners\n"
            "Disallow: /upload\n"
            "Disallow: /webhooks\n"
            "Disallow: /billing\n"
            "Disallow: /shared/\n"
            "\n"
            "Sitemap: https://calloutcome.com/sitemap.xml\n"
        )
        return Response(body, mimetype='text/plain')

    @app.route('/sitemap.xml')
    def sitemap_xml():
        urls = []
        urls.append({
            'loc': 'https://calloutcome.com/welcome',
            'changefreq': 'weekly',
            'priority': '1.0',
        })
        urls.append({
            'loc': 'https://calloutcome.com/blog/',
            'changefreq': 'weekly',
            'priority': '0.8',
        })

        posts_dir = os.path.join(app.root_path, 'blog', 'posts')
        if os.path.isdir(posts_dir):
            for filename in sorted(os.listdir(posts_dir)):
                if filename.endswith('.md'):
                    slug = filename[:-3]
                    filepath = os.path.join(posts_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    lastmod = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime('%Y-%m-%d')
                    urls.append({
                        'loc': f'https://calloutcome.com/blog/{slug}',
                        'lastmod': lastmod,
                        'changefreq': 'monthly',
                        'priority': '0.7',
                    })

        xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for url in urls:
            xml_parts.append('  <url>')
            xml_parts.append(f'    <loc>{url["loc"]}</loc>')
            if 'lastmod' in url:
                xml_parts.append(f'    <lastmod>{url["lastmod"]}</lastmod>')
            xml_parts.append(f'    <changefreq>{url["changefreq"]}</changefreq>')
            xml_parts.append(f'    <priority>{url["priority"]}</priority>')
            xml_parts.append('  </url>')
        xml_parts.append('</urlset>')

        return Response('\n'.join(xml_parts), mimetype='application/xml')

    return app
