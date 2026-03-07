# CallOutcome

## What It Is
A SaaS app that pulls call recordings from Twilio, transcribes them using Twilio Conversational Intelligence, and uses AI to classify whether a job was booked. Results shown on a simple dashboard.

## Tech Stack
- **Framework:** Flask + Jinja2 (server-rendered)
- **Database:** PostgreSQL on Supabase (via SQLAlchemy + Flask-Migrate)
- **Auth:** Flask-Login (session-based)
- **AI Analysis:** Twilio Conversational Intelligence ($0.08/call)
- **CSS:** Pico CSS (classless, CDN)
- **Deploy:** Railway (web + cron)

## Project Structure
```
calloutcome/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── config.py            # Config from env vars
│   ├── models.py            # SQLAlchemy models (Account, Partner, TrackingLine, Call, Invoice)
│   ├── twilio_service.py    # Twilio API helpers
│   ├── decorators.py        # Shared decorators (account_required)
│   ├── phone_utils.py       # get_available_numbers() from Twilio/CallRail
│   ├── sync_utils.py        # spawn_backfill(), spawn_callrail_backfill()
│   ├── auth/routes.py       # Login, signup, logout (checks both Account + Partner)
│   ├── dashboard/routes.py  # Main dashboard, call detail, filters, override
│   ├── lines/routes.py      # CRUD for tracking lines (account-only)
│   ├── onboarding/routes.py # Post-signup wizard (5-step, AJAX)
│   ├── partners/routes.py   # CRUD for partner logins (account-only)
│   ├── webhooks/routes.py   # Twilio CI webhook receiver
│   ├── upload/routes.py     # Manual audio file upload (account-only)
│   ├── templates/           # Jinja2 templates
│   └── static/style.css     # Minimal custom CSS
├── scripts/
│   ├── poll_twilio.py       # Cron: fetch recordings → submit to CI
│   └── setup_twilio_ci.py   # One-time: create CI service + operator
├── migrations/              # Alembic migrations
├── requirements.txt
├── Procfile                 # gunicorn
└── railway.toml             # Railway deploy config
```

## Key Flows
1. **Cron job** (every 5 min) → fetches new Twilio recordings → submits to CI
2. **Manual upload** → user uploads audio → submitted to CI
3. **Twilio CI webhook** → receives transcript + classification → saves to DB
4. **Dashboard** → shows calls with status, filters, stats, detail view with transcript

## Database
- Multi-tenant from day one (account_id on all tables)
- Models: Account, Partner, TrackingLine, Call, Invoice
- Partners are view-only users linked to an Account. TrackingLine has nullable `partner_id` FK.
- Flask-Login uses prefixed IDs (`account:1`, `partner:1`) to distinguish user types.
- Connection via DATABASE_URL env var (Supabase PostgreSQL)

## Current Status
- All code written (Phases 1-7 of plan)
- Needs: Supabase project, Railway deploy, Twilio credentials, initial migration

## Environment Variables
See `.env.example` for full list. Credentials stored in `~/.claude/credentials.env`.
