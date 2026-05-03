"""
auth.py — Authentication module for Expense Tracker v3.
Handles registration, login, session management via bcrypt + SQLite.
"""

import re
from datetime import datetime, timedelta

import streamlit as st
import bcrypt

from db import (
    create_user, get_user_by_username, get_user_by_email,
    username_exists, email_exists,
    update_user_password, get_session, log_audit, init_db
)

# ── Security constants ────────────────────────────────────────────────────────
SESSION_TIMEOUT_MINUTES = 60   # auto-logout after 60 min of inactivity
_MAX_ATTEMPTS           = 5    # failed logins before lockout
_LOCKOUT_MINUTES        = 5    # lockout duration


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        # Surface real errors so they're not silently swallowed
        import streamlit as st
        st.error(f"Auth error (please contact support): {e}")
        return False


# ── Validation helpers ────────────────────────────────────────────────────────

def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _valid_password(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    return True, ""


# ── Rate limiting ────────────────────────────────────────────────────────────

def _is_locked_out(username: str) -> tuple[bool, int]:
    """Returns (is_locked, seconds_remaining)."""
    key     = f"_failed_{username}"
    cutoff  = datetime.now() - timedelta(minutes=_LOCKOUT_MINUTES)
    recent  = [t for t in st.session_state.get(key, []) if t > cutoff]
    st.session_state[key] = recent
    if len(recent) >= _MAX_ATTEMPTS:
        secs = int((_LOCKOUT_MINUTES * 60) - (datetime.now() - recent[0]).total_seconds())
        return True, max(secs, 0)
    return False, 0


def _record_failed_attempt(username: str):
    key = f"_failed_{username}"
    st.session_state.setdefault(key, []).append(datetime.now())


def _reset_attempts(username: str):
    st.session_state.pop(f"_failed_{username}", None)


# ── Core auth functions ───────────────────────────────────────────────────────

def register_user(username: str, email: str, password: str, display_name: str) -> tuple[bool, str]:
    username = username.strip().lower()          # normalise to lowercase
    email    = email.strip().lower()
    password = password.strip()                  # remove accidental leading/trailing whitespace

    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if not all(c.isalnum() or c == "_" for c in username):
        return False, "Username can only contain letters, numbers, and underscores."
    if not _valid_email(email):
        return False, "Please enter a valid email address."

    ok, msg = _valid_password(password)
    if not ok:
        return False, msg

    if username_exists(username):
        return False, "That username is already taken. Please choose another."
    if email_exists(email):
        return False, "An account with that email already exists."

    pw_hash = hash_password(password)
    create_user(username, email, pw_hash, display_name or username)
    return True, "Account created successfully! You can now log in."


def login_user(username: str, password: str) -> tuple[bool, dict | None, str]:
    username = username.strip().lower()          # normalise — matches registration
    password = password.strip()                  # remove accidental whitespace
    user = get_user_by_username(username)

    if not user:
        return False, None, "Incorrect username or password."
    if not verify_password(password, user["password_hash"]):
        return False, None, "Incorrect username or password."

    return True, user, "Welcome back!"


def reset_password_by_email(email: str, new_password: str) -> tuple[bool, str]:
    """Reset password by email verification (local app — email is identity proof)."""
    new_password = new_password.strip()
    ok, msg = _valid_password(new_password)
    if not ok:
        return False, msg
    user = get_user_by_email(email.strip().lower())
    if not user:
        return False, "No account found with that email address."
    update_user_password(user["id"], hash_password(new_password))
    return True, "Password reset successfully. You can now log in."


def change_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str]:
    from db import get_session
    from db import User
    old_password = old_password.strip()
    new_password = new_password.strip()
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if not u:
            return False, "User not found."
        if not verify_password(old_password, u.password_hash):
            return False, "Current password is incorrect."

    ok, msg = _valid_password(new_password)
    if not ok:
        return False, msg

    update_user_password(user_id, hash_password(new_password))
    return True, "Password changed successfully."


def logout():
    for key in ["authenticated", "user_id", "username", "display_name",
                "household_id", "onboarding_complete", "onboarding_step"]:
        st.session_state.pop(key, None)


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_login_page():
    st.markdown("""
    <div style="max-width:420px;margin:60px auto 0 auto;text-align:center;">
        <h1 style="font-size:2.4rem;margin-bottom:4px;">💰 Expense Tracker</h1>
        <p style="color:#888;margin-bottom:32px;">Track smarter. Save better.</p>
    </div>
    """, unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        tab_login, tab_register, tab_forgot = st.tabs(
            ["🔑 Login", "✨ Create Account", "🔓 Forgot Password"]
        )

        # ── Login tab ─────────────────────────────────────────────────────────
        with tab_login:
            with st.form("login_form"):
                username  = st.text_input("Username", placeholder="your_username")
                password  = st.text_input("Password", type="password", placeholder="••••••••")
                submitted = st.form_submit_button("Login →", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    locked, secs = _is_locked_out(username)
                    if locked:
                        mins = secs // 60; sec_r = secs % 60
                        st.error(f"🔒 Too many failed attempts. Please wait "
                                 f"{mins}m {sec_r}s before trying again.")
                    else:
                        ok, user, msg = login_user(username, password)
                        if ok:
                            _reset_attempts(username)
                            st.session_state.authenticated       = True
                            st.session_state.user_id             = user["id"]
                            st.session_state.username            = user["username"]
                            st.session_state.display_name        = user["display_name"]
                            st.session_state.household_id        = user["household_id"]
                            st.session_state.onboarding_complete = user["onboarding_complete"]
                            st.session_state.onboarding_step     = 0
                            st.session_state.last_activity       = datetime.now()
                            st.rerun()
                        else:
                            _record_failed_attempt(username)
                            st.error(f"🔒 {msg}")
                            locked2, secs2 = _is_locked_out(username)
                            if locked2:
                                st.warning(f"Account locked for {_LOCKOUT_MINUTES} minutes "
                                           f"due to too many failed attempts.")

        # ── Register tab ──────────────────────────────────────────────────────
        with tab_register:
            with st.form("register_form"):
                r_display  = st.text_input("Your name", placeholder="e.g. Username")
                r_username = st.text_input("Username", placeholder="min. 3 characters")
                r_email    = st.text_input("Email address", placeholder="you@example.com")
                r_pass     = st.text_input("Password", type="password",
                                           placeholder="min. 8 chars, include a number")
                r_confirm  = st.text_input("Confirm password", type="password", placeholder="••••••••")
                submitted_r = st.form_submit_button("Create Account →",
                                                    use_container_width=True, type="primary")

            if submitted_r:
                if r_pass != r_confirm:
                    st.error("Passwords don't match. Please try again.")
                else:
                    ok, msg = register_user(r_username, r_email, r_pass, r_display)
                    if ok:
                        st.success(f"✅ {msg} Please log in.")
                    else:
                        st.error(f"⚠️ {msg}")

        # ── Forgot password tab ───────────────────────────────────────────────
        with tab_forgot:
            st.caption("Enter your registered email address and choose a new password.")
            with st.form("forgot_form"):
                fp_email   = st.text_input("Email address", placeholder="you@example.com")
                fp_new     = st.text_input("New password", type="password",
                                           placeholder="min. 8 chars, include a number")
                fp_confirm = st.text_input("Confirm new password", type="password",
                                           placeholder="••••••••")
                submitted_f = st.form_submit_button("Reset Password →",
                                                    use_container_width=True, type="primary")

            if submitted_f:
                if fp_new != fp_confirm:
                    st.error("Passwords don't match.")
                else:
                    ok, msg = reset_password_by_email(fp_email, fp_new)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        st.error(f"⚠️ {msg}")

        st.markdown("<br><p style='text-align:center;color:#aaa;font-size:12px;'>"
                    "Your data is stored locally on this device.</p>", unsafe_allow_html=True)


def require_auth() -> bool:
    """Returns True if authenticated and session is active, else shows login page."""
    init_db()
    if st.session_state.get("authenticated"):
        last = st.session_state.get("last_activity")
        if last is not None:
            elapsed_min = (datetime.now() - last).total_seconds() / 60
            if elapsed_min > SESSION_TIMEOUT_MINUTES:
                logout()
                st.warning("⏱️ Your session expired after inactivity. Please log in again.")
                render_login_page()
                return False
        st.session_state.last_activity = datetime.now()
        return True
    render_login_page()
    return False
