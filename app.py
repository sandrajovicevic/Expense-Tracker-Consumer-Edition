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
    SUPPORTED_CURRENCIES, get_rates, get_currency_symbol,
    get_lan_urls, get_server_port, qr_png,
    inject_mobile_css,
)
from gamification import render_gamification_sidebar
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

# ── Shared sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## 👋 {display_name}")

    st.markdown("**Display currency**")
    cur_list  = list(SUPPORTED_CURRENCIES.keys())
    dc_default = settings.get("default_currency", "EUR")
    dc_idx    = cur_list.index(dc_default) if dc_default in cur_list else 0
    DC = st.selectbox("Currency", cur_list, index=dc_idx, key="dc_sidebar")
    st.session_state.dc = DC
    SYM = get_currency_symbol(DC)

    with st.form("rate_form"):
        rsd_val = st.number_input(f"Exchange rate (1 EUR = ? {SYM})",
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
    _exp_df_side = get_expenses(user_id)
    _inc_df_side = get_income(user_id)
    _sav_df_side = get_savings(user_id)
    _bud_df_side = get_budgets(user_id)
    render_gamification_sidebar(_exp_df_side, _inc_df_side, _sav_df_side, _bud_df_side)

    st.divider()
    st.caption("📱 Phone access:")
    st.code(f"http://{get_ip()}:{APP_PORT}", language=None)

    if st.button("🚪 Logout", use_container_width=True):
        logout()
        st.rerun()

# ── Recurring bill & budget alerts ───────────────────────────────────────────
_rec_df_alerts  = get_recurring(user_id)
_exp_df_alerts  = get_expenses(user_id)
_bud_df_alerts  = get_budgets(user_id)
check_and_send_bill_reminders(_rec_df_alerts, _exp_df_alerts, settings)
check_and_send_budget_alerts(user_id, _exp_df_alerts, _bud_df_alerts, settings, rate, DC)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOG EXPENSE
# ══════════════════════════════════════════════════════════════════════════════
if page == "📅 Log Expense":
    st.title("📅 Log Expense")
    help_expander("How to log an expense",
                  "Choose a category first — the subcategory list will update automatically. "
                  "Add a short description so you can search for it later. "
                  "Tick '🔄 Recurring' to also save it as a monthly template.")

    oc1, oc2 = st.columns([3, 1])
    with oc1:
        cat = st.selectbox("Category", CAT_LIST, key="exp_cat_outer")
    with oc2:
        cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="exp_cur_outer")
    sym = _cur_sym(cur)

    with st.form("exp_form", clear_on_submit=False):
        f1, f2 = st.columns(2)
        with f1:
            exp_date = st.date_input("Date", value=date.today())
            subcat   = st.selectbox("Subcategory", ["—"] + CATEGORIES[cat])
        with f2:
            amount  = st.number_input(f"Amount ({sym})", min_value=0.01,
                                      max_value=MAX_AMOUNT, step=0.50, format="%.2f")
            is_rec  = st.checkbox("🔄 Also save as recurring template")
        desc  = st.text_input("Description *", placeholder="e.g. Lidl weekly shop")
        notes = st.text_input("Notes (optional)")
        saved = st.form_submit_button("✅ Save Expense", use_container_width=True, type="primary")

    if saved:
        if not desc.strip():
            safe_error("Please add a description so you can find this expense later.")
        else:
            ae = _eur(amount, cur, rate)
            add_expense(user_id, {
                "date": exp_date, "category": cat,
                "subcategory": subcat if subcat != "—" else "",
                "description": desc, "amount": amount,
                "currency": cur, "amount_eur": ae,
                "recurring": is_rec, "notes": notes,
            })
            if is_rec:
                add_recurring(user_id, {
                    "category": cat,
                    "subcategory": subcat if subcat != "—" else "",
                    "description": desc, "amount": amount,
                    "currency": cur, "amount_eur": ae,
                    "notes": notes, "active": True,
                })
            st.success(f"✅ **{desc}** — {fmt_both(ae, rate)}")
            st.balloons()

    # ── Expense history ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Expense history")
    df_exp = get_expenses(user_id)

    if not df_exp.empty:
        sc1, sc2, sc3 = st.columns([3, 2, 2])
        with sc1: srch = st.text_input("🔍 Search", placeholder="Search description...", key="exp_srch")
        with sc2: catf = st.multiselect("Category filter", CAT_LIST, key="exp_catf")
        with sc3: curf = st.multiselect("Currency filter", list(SUPPORTED_CURRENCIES.keys()), key="exp_curf")

        v = df_exp.sort_values("date", ascending=False).copy()
        if srch: v = v[v["description"].str.contains(srch, case=False, na=False)]
        if catf: v = v[v["category"].isin(catf)]
        if curf: v = v[v["currency"].isin(curf)]

        d = v.head(50).copy()
        d["#"]        = range(len(d))
        d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
        d["Amount"]   = d["amount_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Original"] = d.apply(lambda r: f"{r['amount']:,.2f} {r['currency']}", axis=1)
        d["🔄"]       = d["recurring"].apply(lambda x: "🔄" if str(x).lower() in ("true","1") else "")

        st.dataframe(
            d[["#","Date","category","subcategory","description","Amount","Original","🔄","notes"]],
            use_container_width=True, hide_index=True,
        )

        # Edit / soft-delete
        with st.expander("✏️ Edit or delete a row"):
            row_num = st.number_input("Row # from table above", min_value=0,
                                      max_value=max(0, len(d)-1), step=1, key="exp_edit_idx")
            act = st.radio("Action", ["Edit", "Delete"], horizontal=True, key="exp_act")

            if row_num in d["#"].values:
                sel_id = d[d["#"] == row_num]["id"].iloc[0]
                rd     = df_exp[df_exp["id"] == sel_id].iloc[0]

                if act == "Delete":
                    if st.button("🗑️ Move to Trash", type="secondary"):
                        soft_delete_expense(user_id, sel_id)
                        st.toast("Moved to trash — you can restore it below.", icon="🗑️")
                        st.rerun()
                else:
                    with st.form("exp_edit_form"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            ed  = st.date_input("Date", value=rd["date"].date()
                                                if pd.notna(rd["date"]) else date.today())
                            edd = st.text_input("Description", value=str(rd.get("description","")))
                        with ec2:
                            ea  = st.number_input("Amount", value=float(rd.get("amount",0)),
                                                  min_value=0.01, max_value=MAX_AMOUNT,
                                                  step=0.01, format="%.2f")
                            en  = st.text_input("Notes", value=str(rd.get("notes","")))
                        if st.form_submit_button("💾 Save changes", type="primary"):
                            ec = str(rd.get("currency","EUR"))
                            update_expense(user_id, sel_id, {
                                "date": ed, "description": edd,
                                "amount": ea, "amount_eur": _eur(ea, ec, rate), "notes": en,
                            })
                            st.toast("✅ Updated!", icon="✅")
                            st.rerun()

        # Restore deleted
        df_deleted = get_expenses(user_id, include_deleted=True)
        df_deleted = df_deleted[df_deleted["is_deleted"] == True]
        if not df_deleted.empty:
            with st.expander(f"🗑️ Recently deleted ({len(df_deleted)})"):
                for _, row in df_deleted.iterrows():
                    rc1, rc2, rc3 = st.columns([3, 2, 1])
                    with rc1: st.write(f"{row['description']} — {row['category']}")
                    with rc2: st.write(fmt(row["amount_eur"], DC, rate))
                    with rc3:
                        if st.button("↩️ Restore", key=f"rst_{row['id']}"):
                            restore_expense(user_id, row["id"])
                            st.toast("Expense restored!", icon="↩️")
                            st.rerun()

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download expenses.xlsx", data=to_excel(df_exp),
                               file_name="expenses.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("No expenses yet — add your first one above 👆")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOG INCOME
# ══════════════════════════════════════════════════════════════════════════════
elif page == "💵 Log Income":
    st.title("💵 Log Income")
    help_expander("Budgeted vs actual", "Enter what you *expected* to earn (budgeted) "
                  "and what you *actually* received. This powers your savings rate calculations.")

    oc, _ = st.columns([1, 3])
    with oc:
        cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="inc_cur")
    sym = _cur_sym(cur)

    with st.form("inc_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            inc_date = st.date_input("Date", value=date.today())
            source   = st.selectbox("Source", INCOME_SOURCES)
        with c2:
            budgeted = st.number_input(f"Budgeted ({sym})", min_value=0.0,
                                       max_value=MAX_AMOUNT, step=10.0, format="%.2f")
            actual   = st.number_input(f"Actual ({sym})", min_value=0.0,
                                       max_value=MAX_AMOUNT, step=10.0, format="%.2f")
        notes = st.text_input("Notes")
        saved = st.form_submit_button("✅ Save Income", use_container_width=True, type="primary")

    if saved:
        be = _eur(budgeted, cur, rate)
        ae = _eur(actual,   cur, rate)
        add_income(user_id, {
            "date": inc_date, "source": source,
            "budgeted": budgeted, "actual": actual,
            "currency": cur, "budgeted_eur": be, "actual_eur": ae, "notes": notes,
        })
        st.success(f"✅ {source} — {fmt_both(ae, rate)}")

    dfi = get_income(user_id)
    if not dfi.empty:
        st.divider()
        st.subheader("Income history")
        d = dfi.sort_values("date", ascending=False).head(30).copy()
        d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
        d["Budgeted"] = d["budgeted_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Actual"]   = d["actual_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Original"] = d.apply(lambda r: f"{r['actual']:,.0f} {r['currency']}", axis=1)
        st.dataframe(d[["Date","source","Budgeted","Actual","Original","notes"]],
                     use_container_width=True, hide_index=True)

        with st.expander("🗑️ Delete an income entry"):
            del_ids = dfi["id"].tolist()
            del_labels = [f"{r['date'].strftime('%d %b %Y')} — {r['source']} {fmt(r['actual_eur'], DC, rate)}"
                          for _, r in dfi.iterrows()]
            sel = st.selectbox("Select entry", del_labels, key="inc_del_sel")
            if st.button("🗑️ Move to trash", type="secondary", key="inc_del_btn"):
                sel_idx = del_labels.index(sel)
                soft_delete_income(user_id, del_ids[sel_idx])
                st.toast("Income entry moved to trash.", icon="🗑️")
                st.rerun()

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download income.xlsx", data=to_excel(dfi),
                               file_name="income.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SAVINGS GOALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Savings Goals":
    st.title("🎯 Savings Goals")
    st.caption("Log each deposit. Compound interest is calculated monthly on your running balance.")
    help_expander("How compound interest works",
                  "Each time you log a deposit, the app calculates: "
                  "`new balance = previous_balance × (1 + monthly_rate) + deposit`. "
                  "The monthly rate is your annual interest rate ÷ 12.")

    oc, _ = st.columns([1, 3])
    with oc:
        cur = st.selectbox("Save in", list(SUPPORTED_CURRENCIES.keys()), key="sav_cur")
    sym = _cur_sym(cur)

    with st.form("sav_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            sd  = st.date_input("Date", value=date.today())
            gn  = st.selectbox("Goal", SAVINGS_GOALS)
            tgt = st.number_input(f"Target ({sym})", min_value=0.0,
                                  max_value=MAX_SAVINGS_TARGET, step=100.0, format="%.2f")
        with c2:
            dep  = st.number_input(f"Amount deposited ({sym})", min_value=0.0,
                                   max_value=MAX_AMOUNT, step=10.0, format="%.2f")
            ir   = st.number_input("Annual interest rate (%)", min_value=0.0,
                                   max_value=100.0, step=0.01, format="%.2f",
                                   help="e.g. 4.50 for 4.5% p.a., compounded monthly")
        notes = st.text_input("Notes")
        saved = st.form_submit_button("✅ Save Entry", use_container_width=True, type="primary")

    if saved:
        de = _eur(dep, cur, rate)
        te = _eur(tgt, cur, rate)
        dfs_prev = get_savings(user_id)
        pb = 0.0
        if not dfs_prev.empty:
            pr = dfs_prev[dfs_prev["goal_name"] == gn]
            if not pr.empty:
                pb = float(pr.sort_values("date").iloc[-1]["balance_eur"])
        mr = (ir / 100) / 12
        nb = round(pb * (1 + mr) + de, 4)
        add_savings(user_id, {
            "date": sd, "goal_name": gn, "target_eur": te,
            "deposited": dep, "currency": cur,
            "deposited_eur": de, "interest_rate": ir, "balance_eur": nb, "notes": notes,
        })
        st.success(f"✅ {gn} — new balance: {fmt_both(nb, rate)}")

    dfs = get_savings(user_id)
    if not dfs.empty:
        st.divider()
        st.subheader("Goal progress")
        from insights import savings_projection
        for g in dfs["goal_name"].unique():
            rows  = dfs[dfs["goal_name"] == g].sort_values("date")
            lat   = rows.iloc[-1]
            bal   = float(lat["balance_eur"])
            td    = float(rows["deposited_eur"].sum())
            interest = bal - td
            tr    = rows[rows["target_eur"] > 0]
            tgtv  = float(tr["target_eur"].iloc[-1]) if not tr.empty else 0
            pct   = min(bal / tgtv * 100, 100) if tgtv > 0 else 0
            col   = "#00B050" if pct >= 75 else ("#F4A261" if pct >= 40 else "#E94560")

            gc1, gc2 = st.columns([4, 1])
            with gc1:
                st.markdown(f"**{g}**")
                st.markdown(pbar(pct, col), unsafe_allow_html=True)
                proj = savings_projection(dfs, g)
                proj_str = ""
                if proj["months_to_goal"] and proj["months_to_goal"] > 0 and proj["projected_date"]:
                    proj_str = f" · 🎯 Goal in ~{proj['months_to_goal']}mo ({proj['projected_date'].strftime('%b %Y')})"
                st.caption(
                    f"Balance: **{fmt(bal, DC, rate)}** · "
                    f"Target: {fmt(tgtv, DC, rate) if tgtv > 0 else '—'} · "
                    f"Interest earned: {fmt(interest, DC, rate)} · "
                    f"Rate: {lat['interest_rate']:.2f}%"
                    + proj_str
                )
            with gc2:
                st.metric("", f"{pct:.1f}%" if tgtv > 0 else "—")
            st.write("")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Balance over time")
            fig = px.line(dfs, x="date",
                          y=dfs["balance_eur"].apply(lambda x: to_display(x, DC, rate)),
                          color="goal_name", markers=True,
                          labels={"y": f"Balance ({SYM})", "date": "Date", "goal_name": "Goal"},
                          color_discrete_sequence=CHART_COLORS)
            fig.update_layout(legend_title_text="",
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("Interest rate over time")
            fig2 = px.line(dfs, x="date", y="interest_rate", color="goal_name", markers=True,
                           labels={"interest_rate": "Annual Rate (%)", "date": "Date"},
                           color_discrete_sequence=CHART_COLORS)
            fig2.update_layout(legend_title_text="",
                               plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, use_container_width=True)

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download savings.xlsx", data=to_excel(dfs),
                               file_name="savings.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: RECURRING
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔄 Recurring":
    st.title("🔄 Recurring Expenses")
    st.caption("One-click logging for monthly fixed costs.")
    help_expander("What are recurring expenses?",
                  "These are fixed monthly costs like rent, subscriptions, or utilities. "
                  "Add them here once — then tap 'Log now' each month instead of re-entering everything.")

    dfe   = get_expenses(user_id)
    today = date.today()

    with st.expander("➕ Add new template"):
        oc, _ = st.columns([1, 3])
        with oc:
            rc = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="rec_cur")
        rcat = st.selectbox("Category", CAT_LIST, key="rec_cat")
        with st.form("rec_form", clear_on_submit=False):
            rsym = _cur_sym(rc)
            ra1, ra2 = st.columns(2)
            with ra1:
                rsub  = st.selectbox("Subcategory", ["—"] + CATEGORIES[rcat])
                rdesc = st.text_input("Description", placeholder="e.g. Monthly gym membership")
            with ra2:
                ramt   = st.number_input(f"Typical amount ({rsym})", min_value=0.01,
                                         max_value=MAX_AMOUNT, step=0.50, format="%.2f")
                rnotes = st.text_input("Notes")
            if st.form_submit_button("💾 Save template", type="primary"):
                re_eur = _eur(ramt, rc, rate)
                add_recurring(user_id, {
                    "category": rcat,
                    "subcategory": rsub if rsub != "—" else "",
                    "description": rdesc, "amount": ramt,
                    "currency": rc, "amount_eur": re_eur,
                    "notes": rnotes, "active": True,
                })
                st.success(f"✅ {rdesc} saved as template!")
                st.rerun()

    dfr    = get_recurring(user_id)
    active = dfr[dfr["active"] == True] if not dfr.empty else pd.DataFrame()

    if active.empty:
        st.info("No active templates yet. Add one above, or tick '🔄 Recurring' when logging an expense.")
    else:
        st.subheader(f"Monthly checklist — {calendar.month_name[today.month]} {today.year}")
        logged = set()
        if not dfe.empty:
            tm = dfe[(dfe["date"].dt.year == today.year) & (dfe["date"].dt.month == today.month)]
            logged = set(tm["description"].str.strip().tolist())

        for idx, row in active.iterrows():
            done = row["description"].strip() in logged
            rc1, rc2, rc3, rc4 = st.columns([3, 2, 1.5, 1])
            with rc1:
                ic = "✅" if done else "⏳"
                st.markdown(
                    f"{ic} **{row['description']}**  \n"
                    f"<span style='color:#888;font-size:12px;'>"
                    f"{row['category']}{' › '+row['subcategory'] if row['subcategory'] else ''}"
                    f"</span>", unsafe_allow_html=True
                )
            with rc2:
                st.write(fmt(float(row["amount_eur"]), DC, rate))
            with rc3:
                if done:
                    st.success("Logged ✓")
                elif st.button("Log now", key=f"lr_{idx}", type="primary"):
                    add_expense(user_id, {
                        "date": today, "category": row["category"],
                        "subcategory": row["subcategory"],
                        "description": row["description"],
                        "amount": float(row["amount"]),
                        "currency": str(row["currency"]),
                        "amount_eur": float(row["amount_eur"]),
                        "recurring": True, "notes": str(row.get("notes","")),
                    })
                    st.rerun()
            with rc4:
                if st.button("Remove", key=f"dr_{idx}", type="secondary"):
                    update_recurring(user_id, row["id"], {"active": False})
                    st.rerun()
            st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Dashboard":
    st.title("📊 Dashboard")

    dfe = get_expenses(user_id)
    dfi = get_income(user_id)
    dfs = get_savings(user_id)
    dfb = get_budgets(user_id)

    # Household toggle
    hh_id = st.session_state.get("household_id")
    if hh_id:
        view = st.radio("View", ["My data", "Household"], horizontal=True, key="dash_view")
        if view == "Household":
            dfe = get_household_expenses(hh_id)

    if dfe.empty and dfi.empty:
        st.info("No data yet — start logging expenses or income 👈")
        st.stop()

    ayrs = sorted(set(
        (list(dfe["date"].dropna().dt.year.unique()) if not dfe.empty else []) +
        (list(dfi["date"].dropna().dt.year.unique()) if not dfi.empty else [])
    ), reverse=True) or [date.today().year]

    fc1, fc2 = st.columns([1, 2])
    with fc1:
        sy = st.selectbox("Year", ayrs)
    with fc2:
        mo_opts = ["All months"] + [calendar.month_name[m] for m in range(1, 13)]
        sml = st.select_slider("Month", mo_opts)
        sm  = mo_opts.index(sml)

    def flt(df):
        if df.empty: return df
        mask = df["date"].dt.year == sy
        if sm > 0: mask = mask & (df["date"].dt.month == sm)
        return df[mask]

    exp   = flt(dfe)
    inc   = flt(dfi)
    svyr  = dfs[dfs["date"].dt.year == sy] if not dfs.empty else dfs

    ie = float(inc["actual_eur"].sum())  if not inc.empty  else 0.0
    ee = float(exp["amount_eur"].sum())  if not exp.empty  else 0.0
    sd = float(svyr["deposited_eur"].sum()) if not svyr.empty else 0.0
    ne = ie - ee - sd
    sr = (sd / ie * 100) if ie > 0 else 0.0

    st.divider()

    # Phone access panel
    st.markdown("**📱 Phone access**")
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
            st.caption(f"or http://{hostname}:{port}")
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

# ── Page routing ──────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("app_pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("app_pages/log_expense.py", title="Log expense", icon=":material/receipt_long:"),
    st.Page("app_pages/log_income.py", title="Log income", icon=":material/payments:"),
    st.Page("app_pages/savings.py", title="Savings goals", icon=":material/savings:"),
    st.Page("app_pages/portfolio.py", title="Portfolio", icon=":material/trending_up:"),
    st.Page("app_pages/recurring.py", title="Recurring", icon=":material/event_repeat:"),
    st.Page("app_pages/loans.py", title="Loans", icon=":material/account_balance:"),
    st.Page("app_pages/big_purchases.py", title="Big purchases", icon=":material/shopping_bag:"),
    st.Page("app_pages/forecast.py", title="Forecast", icon=":material/query_stats:"),
    st.Page("app_pages/insights_view.py", title="Insights", icon=":material/lightbulb:"),
    st.Page("app_pages/bank_import_view.py", title="Bank import", icon=":material/account_balance_wallet:"),
    st.Page("app_pages/audit_log.py", title="Audit log", icon=":material/history:"),
    st.Page("app_pages/household.py", title="Household", icon=":material/groups:"),
    st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
])
pg.run()
