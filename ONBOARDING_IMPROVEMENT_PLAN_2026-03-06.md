# CallOutcome — Onboarding Improvement Plan

Created: 2026-03-06

---

## Current Onboarding Flow (as-is)

1. **Landing page** → Click "Start Free"
2. **Sign up** → Name, email, password → redirected to Dashboard
3. **Empty Dashboard** with a 3-step checklist:
   - Step 1: Go to Settings → paste Twilio SID/Token or CallRail API key
   - Step 2: Go to Partners → create partner (name + cost/lead)
   - Step 3: Go to Lines → pick number from dropdown, assign to partner
4. After connecting a source, redirect to Backsync page → import historical calls
5. Once all 3 steps done → calls appear on dashboard

---

## Key Friction Points Identified

### 1. "Dump and bounce" after signup
User signs up → sees an empty dashboard with zeros everywhere → has to read a checklist → navigate to 3 different pages (Settings, Partners, Lines). There's no guided flow. The value proposition ("see which calls booked") is invisible until they complete all steps.

### 2. API key pasting is intimidating
Target users are tradies and lead gen operators, not developers. Asking them to find a Twilio Account SID + Auth Token, or a CallRail API key, is the single biggest drop-off risk. The help text is decent, but it's still "go to another website, find a hidden key, copy it, come back, paste it."

### 3. Partners step is mandatory but not always needed
The checklist blocks completion until you have partners AND lines. But many users might just want to score their own calls first — they don't have a partner/tradie setup yet. This step being mandatory delays time-to-value.

### 4. No sample/demo data
A new user can't see what the product does until they've connected everything. There's no way to experience the "aha moment" before committing to the setup work.

### 5. No real-time validation feedback
When pasting API keys, the form does a full page reload to validate. No loading indicator, no inline success/error states.

### 6. No email at any point
No welcome email, no setup reminder, no password reset. If someone signs up and drops off at step 1, there's no way to bring them back.

---

## Proposed Improvements (ranked by impact)

### HIGH IMPACT — do these first

#### 1. Post-signup onboarding wizard (replace the checklist)
Instead of dumping users on an empty dashboard, redirect new signups to `/onboarding` — a single-page, multi-step wizard:

- **Step 1:** "How do you track calls?" → Two big buttons: "CallRail" or "Twilio" (or "I'll upload recordings manually")
- **Step 2:** Paste API key (inline, same page) with validation + success animation
- **Step 3:** "We found these numbers" → auto-show all their phone numbers as checkboxes → one click to create tracking lines
- **Step 4:** (Optional) "Do you send leads to partners?" → Yes/No → if yes, add partner inline
- **Step 5:** Backsync → "Import your last 7 days of calls" → redirect to dashboard with data

This collapses 4 pages into one flow. The user never has to figure out where to go next.

#### 2. Auto-create tracking lines from connected source
After connecting Twilio/CallRail, fetch all phone numbers and present them as a checklist: "We found 6 numbers on your account. Select the ones you want to track." One click → all lines created. Right now users have to manually add each line one by one.

#### 3. Make partners optional in the setup flow
Remove "Add partners" from the mandatory checklist. Let users score calls without partners first — they can add partners later. The checklist should complete after: (1) connect source + (2) assign at least one line. Partners are a "nice to set up later" feature.

#### 4. Sample/demo dashboard
Add a "See how it works" link on the landing page or post-signup that shows a dashboard with 10-20 sample calls (fake data — a plumber, electrician, locksmith scenario). This lets users see the product value before committing to setup. Could even show this as a read-only view right after signup while the checklist is incomplete.

### MEDIUM IMPACT — strong quality-of-life improvements

#### 5. Google Sign-In (OAuth)
Add "Sign up with Google" button. Reduces signup friction significantly. Most SaaS users expect this. Use Google's OAuth2 — Flask has `Authlib` or `Flask-Dance` for this. Free.

#### 6. Welcome email + setup nudges
Using Resend (free tier: 3,000 emails/month, dead simple API):
- Welcome email on signup with a "Complete your setup" CTA
- Reminder email 24 hours later if they haven't connected a source
- "Your first calls are scored!" email when first results come in

This recovers users who sign up but drop off before connecting.

#### 7. Inline API key validation with loading state
When the user pastes a Twilio SID or CallRail key and clicks "Connect", show a spinner → then inline green check "Connected!" or red X "Invalid key — check and try again." No full page reload. Use a simple `fetch()` call to an AJAX endpoint. Makes the connection feel instant and trustworthy.

#### 8. CallRail OAuth2
CallRail has an OAuth2 integration program (technology partner application). If approved, users get a "Connect with CallRail" button → they authorise → token received automatically. No key pasting at all. Worth applying — it's the gold standard UX.

For Twilio, they have "Twilio Connect" (OAuth-like subaccount authorisation for third-party apps). Same idea — users click "Connect" → authorise → done. Worth exploring.

### NICE TO HAVE — polish

#### 9. Short onboarding video
A 60-second Loom video embedded on the onboarding page showing: "Here's how to find your CallRail API key" with a screen recording. Way more effective than text instructions for non-technical users. Free with Loom.

#### 10. Simple analytics: where users drop off
Add PostHog (free tier, self-serve) or even just log events to the DB: `signup_completed`, `source_connected`, `first_line_created`, `first_call_scored`. This tells you exactly where users abandon and what to fix next.

#### 11. Live chat widget
Crisp (free tier) or Intercom — if a user is stuck on the API key step, they can ask for help immediately instead of abandoning. For early-stage, even a simple "Need help? Email us" link would work.

---

## Priority Summary

| Priority | What | Effort | Impact | Status |
|----------|------|--------|--------|--------|
| 1 | Onboarding wizard (replace checklist) | ~4-6 hours | Huge — eliminates the #1 drop-off point | **DONE** (2026-03-06) |
| 2 | Auto-create lines from source | ~1-2 hours | High — removes tedious manual step | **DONE** (2026-03-06) — built into wizard Step 3 |
| 3 | Make partners optional | ~30 min | High — unblocks faster time-to-value | **DONE** (2026-03-06) — Step 4 is skippable |
| 4 | Welcome email via Resend | ~2-3 hours | High — recovers drop-offs | |
| 5 | Sample/demo dashboard | ~2-3 hours | Medium — shows value before setup | |
| 6 | Google Sign-In | ~2-3 hours | Medium — reduces signup friction | |
| 7 | Inline API validation (AJAX) | ~1-2 hours | Medium — feels more polished | **DONE** (2026-03-06) — built into wizard Step 2 |
| 8 | CallRail/Twilio OAuth | Days + approval | Medium-High — but requires partner program | |

---

## Completed: First Bundle (2026-03-06)

Items 1 + 2 + 3 + 7 shipped together as the onboarding wizard (`/onboarding`):

**What was built:**
- 5-step wizard at `/onboarding` replacing the old 3-step dashboard checklist
- Step 1: Choose source (Twilio / CallRail / Manual upload)
- Step 2: Paste API credentials with inline AJAX validation + loading spinner + success/error states
- Step 3: Auto-fetch phone numbers from connected source, display as checkboxes, bulk-create tracking lines
- Step 4: Add partners (optional, skippable)
- Step 5: Import historical calls (backsync) with plan usage display
- New signup → wizard; login with incomplete onboarding → wizard; dashboard guards redirect to wizard
- Existing connected users auto-marked `onboarding_completed=True` via migration
- Shared utilities extracted: `decorators.py`, `phone_utils.py`, `sync_utils.py`
- Mobile responsive (cards stack on small screens)

**Files created:** `app/onboarding/`, `app/decorators.py`, `app/phone_utils.py`, `app/sync_utils.py`, migration `l2m3n4o5p6q7`
**Files modified:** `models.py`, `__init__.py`, `auth/routes.py`, `dashboard/routes.py`, `dashboard/index.html`, `lines/routes.py`, `settings/routes.py`, `partners/routes.py`, `style.css`
