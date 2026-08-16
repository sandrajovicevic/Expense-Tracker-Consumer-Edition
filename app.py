"""
app.py — Expense Tracker v4 — Consumer Edition
Main Streamlit entry point: auth/onboarding gates, shared sidebar, alerts,
and st.navigation-based page routing (pages live in app_pages/).
"""

import streamlit as st

import queries as q
from db import init_db, backup_db, get_settings
from auth import require_auth, logout
from onboarding import render_onboarding
from utils import (
    SUPPORTED_CURRENCIES, get_rates,
    get_lan_urls, get_server_port, qr_png,
    inject_mobile_css, TLS_ENABLED,
)
from gamification import (
    render_gamification_sidebar, get_earned_milestones, award_new_milestones,
)
from notifications import (
    check_and_send_budget_alerts, check_and_send_bill_reminders,
    check_and_send_weekly_summary, check_loan_reminders,
)
from rates import refresh_rates_if_due
from market_data import maybe_refresh_in_background

# ── Page config & boot ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="💰 Expense Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="auto",
)
inject_mobile_css()
init_db()
backup_db()

# ── Auth gate ─────────────────────────────────────────────────────────────────
if not require_auth():
    st.stop()

# ── Shared session state ──────────────────────────────────────────────────────
user_id      = st.session_state.user_id
display_name = st.session_state.display_name
if "db_version" not in st.session_state:
    st.session_state.db_version = 0
st.session_state.settings = get_settings(user_id)
# Refresh exchange rates on login when they're older than 3 days
# (keeps the last known values on any network failure)
st.session_state.settings, _ = refresh_rates_if_due(user_id, st.session_state.settings)

# ── Helpers ───────────────────────────────────────────────────────────────────

def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def _cur_sym(cur: str) -> str:
    return "€" if cur == "EUR" else get_currency_symbol(cur)


def _eur(amount: float, currency: str, rate: float) -> float:
    if currency == "EUR":
        return amount
    return round(amount / rate, 4)


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def render_onboarding():
    step = st.session_state.get("onboarding_step", 0)

    if step == 0:
        st.markdown(f"""
        <div style="text-align:center;padding:40px 0 20px;">
            <div style="font-size:4rem;">👋</div>
            <h1>Welcome, {display_name}!</h1>
            <p style="color:#666;font-size:1.1rem;max-width:500px;margin:0 auto;">
                Let's get you set up. It only takes 2 minutes.
            </p>
        </div>
        """, unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        for col, icon, title, desc in [
            (c1, "📊", "Track Expenses", "Log every purchase — it takes seconds."),
            (c2, "🎯", "Set Budgets",    "Define spending limits per category."),
            (c3, "💡", "Get Insights",   "See where your money is going automatically."),
        ]:
            with col:
                st.markdown(f"""
                <div class="kpi">
                    <div style="font-size:2rem;">{icon}</div>
                    <div class="kpi-val">{title}</div>
                    <div class="kpi-sub">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

        st.write("")
        _, btn_col, _ = st.columns([2, 1, 2])
        with btn_col:
            if st.button("Let's get started →", type="primary", use_container_width=True):
                st.session_state.onboarding_step = 1
                st.rerun()

    elif step == 1:
        st.title("Step 1 of 2 — Your Currency & Budget")
        st.caption("You can change these any time in Settings.")

        settings = get_settings(user_id)
        with st.form("onboard_step1"):
            dc = st.selectbox("Display currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=0, help="The currency you'll see amounts in.")
            rate_val = st.number_input("Exchange rate (1 EUR = ? in local currency)",
                                       value=float(settings.get("exchange_rate", 117.0)),
                                       step=1.0, format="%.2f",
                                       help="If you use EUR only, leave this as-is.")
            budget   = st.number_input("Monthly budget (EUR)",
                                       min_value=0.0, step=100.0, format="%.2f",
                                       help="Your total spending limit per month. You can set category limits later.")
            if st.form_submit_button("Save & Continue →", type="primary", use_container_width=True):
                save_settings(user_id, {
                    "default_currency": dc,
                    "exchange_rate": rate_val,
                    "monthly_budget": budget,
                })
                st.session_state.onboarding_step = 2
                st.rerun()

    elif step == 2:
        st.title("Step 2 of 2 — Log Your First Expense")
        st.caption("Or skip — you can log expenses anytime from the main menu.")

        settings = get_settings(user_id)
        rate     = float(settings.get("exchange_rate", 117.0))

        with st.form("onboard_exp"):
            c1, c2 = st.columns(2)
            with c1:
                exp_date = st.date_input("Date", value=date.today())
                cat      = st.selectbox("Category", CAT_LIST)
            with c2:
                amount = st.number_input("Amount (€)", min_value=0.01,
                                         max_value=MAX_AMOUNT, step=1.0, format="%.2f")
                desc   = st.text_input("Description", placeholder="e.g. Weekly groceries")

            c_save, c_skip = st.columns(2)
            with c_save:
                saved = st.form_submit_button("Save & Finish ✅", type="primary", use_container_width=True)
            with c_skip:
                skipped = st.form_submit_button("Skip for now →", use_container_width=True)

        if saved:
            if desc.strip():
                add_expense(user_id, {
                    "date": exp_date, "category": cat, "subcategory": "",
                    "description": desc, "amount": amount,
                    "currency": "EUR", "amount_eur": _eur(amount, "EUR", rate),
                    "recurring": False, "notes": "",
                })
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.success("🎉 You're all set! Welcome to your Expense Tracker.")
            st.balloons()
            st.rerun()

        if skipped:
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.rerun()


# ── Run onboarding if not complete ───────────────────────────────────────────
if not st.session_state.get("onboarding_complete", True):
    render_onboarding()
    st.stop()

settings = st.session_state.settings
rates    = get_rates(settings)
st.session_state.rates = rates

# ── Milestone unlocks & rewards (persisted once; fun-money bonuses) ───────────
earned_ms = get_earned_milestones(
    q.expenses(user_id), q.income(user_id), q.savings(user_id), q.budgets(user_id),
    settings=settings, loans_df=q.loans(user_id),
)
new_ms, ms_bonus = award_new_milestones(user_id, earned_ms, settings)
if new_ms:
    names = ", ".join(f"{m['icon']} {m['title']}" for m in new_ms)
    st.toast(f"🏅 Milestone unlocked: {names}"
             + (f" — +€{ms_bonus:.0f} fun money next month!" if ms_bonus > 0 else ""),
             icon="🏅")
    st.balloons()
    settings = st.session_state.settings  # refresh after reward save

# ── Shared sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## 👋 {display_name}")

    st.markdown("**Display currency**")
    cur_list  = list(SUPPORTED_CURRENCIES.keys())
    dc_default = settings.get("default_currency", "EUR")
    dc_idx    = cur_list.index(dc_default) if dc_default in cur_list else 0
    DC = st.selectbox("Currency", cur_list, index=dc_idx, key="dc_sidebar")
    st.session_state.dc = DC

    with st.form("rate_form"):
        rsd_val = st.number_input("Exchange rate (1 EUR = ? din)",
                                  value=float(rates.get("RSD", 117.0)),
                                  step=1.0, format="%.2f")
        saved_rate = st.form_submit_button("💱 Update rate")
    if saved_rate:
        new_rates = dict(st.session_state.settings.get("currency_rates") or {})
        new_rates["RSD"] = float(rsd_val)
        q.save_settings(user_id, {"currency_rates": new_rates})
        st.rerun()
    st.caption(f"1 EUR = {rates['RSD']:.2f} din · other rates in ⚙️ Settings")

    st.divider()

    # Gamification
    render_gamification_sidebar(
        q.expenses(user_id), q.income(user_id),
        q.savings(user_id), q.budgets(user_id),
        settings=settings, loans_df=q.loans(user_id),
    )

    st.divider()

    # Phone access panel (experimental)
    st.markdown("**📱 Phone access** 🧪")
    port = get_server_port()
    urls, hostname = get_lan_urls(port)
    if urls:
        st.code(urls[0], language=None)
        qr_bytes = qr_png(urls[0])
        st.image(qr_bytes, width=220)
        st.download_button(
            "⬇️ Download QR code", data=qr_bytes,
            file_name="expense_tracker_qr.png", mime="image/png",
            key="dl_qr", width="stretch",
        )
        st.caption("Scan with your phone camera — same Wi-Fi network.")
        if hostname:
            scheme = "https" if TLS_ENABLED else "http"
            st.caption(f"or {scheme}://{hostname}:{port}")
        st.caption("🧪 Phone access & sync are **experimental** — "
                   "see Settings → Sync for pairing.")
    else:
        st.caption("Start the server with `run_server.bat` and allow Private network access in the firewall prompt.")

    st.divider()

    if st.button("🚪 Logout", width="stretch"):
        logout()
        st.rerun()

# ── Recurring bill & budget alerts (once per rerun; session-state deduped) ────
settings = st.session_state.settings
DC       = st.session_state.dc
check_and_send_bill_reminders(user_id, q.recurring(user_id), q.expenses(user_id), settings)
check_and_send_budget_alerts(user_id, q.expenses(user_id), q.budgets(user_id), settings, rates, DC)
check_loan_reminders(user_id, q.loans(user_id), q.expenses(user_id), settings)
check_and_send_weekly_summary(user_id, q.expenses(user_id), settings)
# Portfolio prices refresh daily in the background (never blocks the UI)
maybe_refresh_in_background(user_id)

# ── Page routing (grouped) ────────────────────────────────────────────────────
pg = st.navigation({
    "Overview": [
        st.Page("app_pages/dashboard.py", title="Dashboard",
                icon=":material/dashboard:", default=True),
    ],
    "Track": [
        st.Page("app_pages/log_expense.py", title="Log expense", icon=":material/receipt_long:"),
        st.Page("app_pages/log_income.py", title="Log income", icon=":material/payments:"),
        st.Page("app_pages/savings.py", title="Savings goals", icon=":material/savings:"),
        st.Page("app_pages/bank_import_view.py", title="Bank import", icon=":material/account_balance_wallet:"),
    ],
    "Plan": [
        st.Page("app_pages/recurring.py", title="Recurring", icon=":material/event_repeat:"),
        st.Page("app_pages/loans.py", title="Loans", icon=":material/account_balance:"),
        st.Page("app_pages/big_purchases.py", title="Big purchases", icon=":material/shopping_bag:"),
        st.Page("app_pages/travel.py", title="Travel budget", icon=":material/flight:"),
        st.Page("app_pages/portfolio.py", title="Portfolio", icon=":material/trending_up:"),
    ],
    "Understand": [
        st.Page("app_pages/forecast.py", title="Forecast", icon=":material/query_stats:"),
        st.Page("app_pages/insights_view.py", title="Insights", icon=":material/lightbulb:"),
    ],
    "Household & Data": [
        st.Page("app_pages/household.py", title="Household", icon=":material/groups:"),
        st.Page("app_pages/audit_log.py", title="Audit log", icon=":material/history:"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
    ],
})
pg.run()
