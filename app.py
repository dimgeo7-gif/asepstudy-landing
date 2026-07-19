import streamlit as st
import streamlit.components.v1 as components
import json
import random
import time
import os
import httpx
from datetime import datetime

st.set_page_config(page_title="🎯 Έξυπνος Προσομοιωτής Διαγωνισμού", page_icon="🎯", layout="centered")

st.markdown("""
<style>
    /* Γενικό typography */
    .stApp { font-family: 'Segoe UI', system-ui, sans-serif; }

    /* Sidebar πιο σκούρο/καθαρό */
    section[data-testid="stSidebar"] {
        background-color: #f7f8fa;
        border-right: 1px solid #e4e7eb;
    }

    /* Κάρτες (containers με border) πιο "ζωντανές" με hover */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        transition: box-shadow 0.15s ease-in-out;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
    }

    /* Κουμπιά πιο στρογγυλά */
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
    }
    .stButton > button[kind="primary"] {
        background-color: #e8491d;
        border-color: #e8491d;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #c93d17;
        border-color: #c93d17;
    }

    /* Metrics πιο ευανάγνωστα */
    div[data-testid="stMetricValue"] { font-weight: 700; }

    /* Progress bar πιο χοντρή */
    div[data-testid="stProgress"] > div > div { height: 10px; border-radius: 5px; }

    /* Mobile: λιγότερο πλαϊνό padding στο κύριο block */
    @media (max-width: 640px) {
        .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 2rem; }
        div[data-testid="column"] { min-width: 100% !important; }
    }
</style>
""", unsafe_allow_html=True)

# --- JS BRIDGE: hash fragment -> query param ---
# Το link επαναφοράς κωδικού του Supabase καταλήγει σε URL της μορφής
# "...#access_token=xxx&type=recovery&..." (hash fragment). Τα hash fragments
# ΔΕΝ στέλνονται ποτέ στον server - μόνο ο browser τα βλέπει, άρα ο Python
# κώδικας του Streamlit δεν μπορεί να τα διαβάσει απευθείας. Αυτό το μικρό
# script τρέχει σε κάθε φόρτωση σελίδας, ελέγχει αν υπάρχει τέτοιο hash, και
# αν ναι, ξανακατευθύνει τον browser στο ΙΔΙΟ URL αλλά με το token σαν κανονικό
# query parameter (?recovery_token=xxx) - αυτό ΜΠΟΡΕΙ να το διαβάσει το
# st.query_params στο Python.
components.html("""
<script>
(function() {
    const hash = window.parent.location.hash;
    if (hash && hash.includes('type=recovery') && hash.includes('access_token=')) {
        const params = new URLSearchParams(hash.substring(1));
        const token = params.get('access_token');
        if (token) {
            const url = new URL(window.parent.location.href);
            url.hash = '';
            url.searchParams.set('recovery_token', token);
            window.parent.location.href = url.toString();
        }
    }
})();
</script>
""", height=0, width=0)

# --- 1. CONFIG & ENV KEYS ---
def get_env_keys():
    try:
        if dict(st.secrets): return dict(st.secrets)
    except: pass
    env = {}
    if os.path.exists(".env.local"):
        with open(".env.local", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env[k] = v.strip('"')
    # Fallback σε πραγματικά OS environment variables - αυτό χρειάζεται για
    # hosts όπως το Render.com, που δίνουν μεταβλητές περιβάλλοντος μέσα από
    # ένα dashboard, όχι μέσα από secrets.toml/.env.local αρχείο.
    for key in ("NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY",
                "SUPABASE_SERVICE_ROLE_KEY", "ADMIN_EMAIL", "RESEND_API_KEY"):
        if key not in env and key in os.environ:
            env[key] = os.environ[key]
    return env

ENV = get_env_keys()
URL = ENV.get("NEXT_PUBLIC_SUPABASE_URL")
ANON_KEY = ENV.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
SERVICE_ROLE_KEY = ENV.get("SUPABASE_SERVICE_ROLE_KEY")  # ΜΟΝΟ για admin ενέργειες - ποτέ στον client
ADMIN_EMAIL = ENV.get("ADMIN_EMAIL")
RESEND_API_KEY = ENV.get("RESEND_API_KEY")

if not URL or not ANON_KEY:
    st.error(
        "⚠️ Λείπουν τα Supabase credentials (NEXT_PUBLIC_SUPABASE_URL / "
        "NEXT_PUBLIC_SUPABASE_ANON_KEY).\n\n"
        "Αν τρέχεις στο Streamlit Cloud: Settings → Secrets και πρόσθεσε:\n"
        "```\nNEXT_PUBLIC_SUPABASE_URL = \"https://xxxx.supabase.co\"\n"
        "NEXT_PUBLIC_SUPABASE_ANON_KEY = \"eyJ...\"\n```\n\n"
        "Αν τρέχεις τοπικά: δημιούργησε `.env.local` με τις ίδιες μεταβλητές."
    )
    st.stop()

# --- 2. SUPABASE AUTH & DATA ---
def _friendly_auth_error(response):
    """Μετατρέπει το raw error του Supabase Auth σε κατανοητό μήνυμα στα ελληνικά."""
    try:
        err = response.json()
        raw_msg = err.get("error_description") or err.get("msg") or err.get("error") or ""
    except Exception:
        raw_msg = ""
    low = raw_msg.lower()
    if "invalid login credentials" in low or "invalid_grant" in low:
        return "❌ Λάθος email ή κωδικός."
    if "email not confirmed" in low:
        return "📧 Το email δεν έχει επιβεβαιωθεί ακόμα. Έλεγξε τα εισερχόμενά σου."
    if "already registered" in low or "already exists" in low or "user_already_exists" in low:
        return "⚠️ Υπάρχει ήδη λογαριασμός με αυτό το email. Δοκίμασε να συνδεθείς."
    if "password" in low and ("short" in low or "at least" in low or "characters" in low):
        return "⚠️ Ο κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες."
    if "unable to validate email" in low or "invalid email" in low:
        return "⚠️ Μη έγκυρο email."
    if "rate limit" in low:
        return "⏳ Πολλές προσπάθειες. Δοκίμασε ξανά σε λίγο."
    if raw_msg:
        return f"❌ {raw_msg}"
    return f"❌ Σφάλμα (HTTP {response.status_code}). Δοκίμασε ξανά."

def supabase_login(email, password):
    """Επιστρέφει (session_dict, error_message). Ένα από τα δύο είναι πάντα None."""
    auth_url = f"{URL}/auth/v1/token?grant_type=password"
    headers = {"apikey": ANON_KEY, "Content-Type": "application/json"}
    payload = {"email": email, "password": password}
    try:
        response = httpx.post(auth_url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            return {"user_id": data["user"]["id"], "token": data["access_token"], "email": data["user"].get("email")}, None
        return None, _friendly_auth_error(response)
    except Exception as e:
        return None, f"❌ Σφάλμα δικτύου: {e}"

def supabase_signup(email, password):
    """Επιστρέφει (success: bool, error_message)."""
    auth_url = f"{URL}/auth/v1/signup"
    headers = {"apikey": ANON_KEY, "Content-Type": "application/json"}
    payload = {"email": email, "password": password}
    try:
        response = httpx.post(auth_url, headers=headers, json=payload)
        if response.status_code in (200, 201):
            return True, None
        return False, _friendly_auth_error(response)
    except Exception as e:
        return False, f"❌ Σφάλμα δικτύου: {e}"

def supabase_request_password_reset(email, redirect_to):
    """Στέλνει email επαναφοράς κωδικού. Επιστρέφει (success: bool, error_message)."""
    auth_url = f"{URL}/auth/v1/recover"
    headers = {"apikey": ANON_KEY, "Content-Type": "application/json"}
    payload = {"email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to
    try:
        response = httpx.post(auth_url, headers=headers, json=payload)
        # Το Supabase επιστρέφει 200 ακόμα κι αν το email δεν υπάρχει (για να μην
        # αποκαλύπτει ποια emails είναι εγγεγραμμένα - security best practice).
        if response.status_code == 200:
            return True, None
        return False, _friendly_auth_error(response)
    except Exception as e:
        return False, f"❌ Σφάλμα δικτύου: {e}"

def supabase_update_password(access_token, new_password):
    """Ορίζει νέο κωδικό χρησιμοποιώντας το access_token από το recovery link."""
    auth_url = f"{URL}/auth/v1/user"
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        response = httpx.put(auth_url, headers=headers, json={"password": new_password})
        if response.status_code == 200:
            return True, None
        return False, _friendly_auth_error(response)
    except Exception as e:
        return False, f"❌ Σφάλμα δικτύου: {e}"

@st.cache_data
def load_questions():
    local_source_mapping = {}
    if os.path.exists("questions.json"):
        try:
            with open("questions.json", "r", encoding="utf-8") as f:
                for item in json.load(f):
                    if "question" in item and "source" in item:
                        local_source_mapping[item["question"]] = item["source"]
        except: pass

    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}
    try:
        # ΣΗΜΑΝΤΙΚΟ: το Supabase/PostgREST περιορίζει από προεπιλογή τα
        # αποτελέσματα ενός request (συνήθως στις 1000 γραμμές). Χωρίς
        # pagination, ένα απλό GET θα επέστρεφε μόνο τις πρώτες ~1000 από
        # τις 1988 ερωτήσεις - οι υπόλοιπες θα "χάνονταν" σιωπηλά. Εδώ
        # τραβάμε σε batches των 1000 μέχρι να αδειάσουν τα αποτελέσματα.
        raw_data = []
        batch_size = 1000
        offset = 0
        while True:
            response = httpx.get(
                f"{URL}/rest/v1/questions",
                headers=headers,
                params={"select": "*", "limit": batch_size, "offset": offset},
            )
            response.raise_for_status()
            batch = response.json()
            raw_data.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size

        mapped_questions = []
        skipped = 0
        for q in raw_data:
            try:
                q_text = q.get("question_text")
                # ΣΗΜΑΝΤΙΚΟ: .get(key, default) επιστρέφει default ΜΟΝΟ αν λείπει το
                # key - αν υπάρχει με τιμή None (π.χ. category IS NULL στη βάση),
                # επιστρέφει None και το επόμενο .lower() θα έσκαγε, ρίχνοντας ΟΛΗ
                # τη λίστα ερωτήσεων (λόγω του εξωτερικού except). Το "or" εδώ
                # καλύπτει και τις δύο περιπτώσεις (λείπει Ή είναι None).
                actual_source = local_source_mapping.get(q_text) or q.get("category") or "Γενικές Γνώσεις"
                clean_source = actual_source[:-4] if actual_source.lower().endswith('.pdf') else actual_source
                parts = clean_source.split('.', 1)
                if len(parts) > 1 and parts[0].isdigit(): clean_source = parts[1]

                mapped_questions.append({
                    "category": q.get("category"), "source": clean_source.strip(),
                    "question": q_text, "options": q.get("options"), "correct": q.get("correct_option")
                })
            except Exception:
                skipped += 1
                continue  # μία προβληματική γραμμή δεν πρέπει να ρίχνει όλη τη λίστα

        if skipped:
            record_sync_error(f"load_questions: παραλείφθηκαν {skipped} προβληματικές ερωτήσεις")
        return mapped_questions
    except Exception as e:
        record_sync_error(f"load_questions: {e}")
        return []

def record_sync_error(msg):
    """Συσσωρεύει σφάλματα αντί να κρατάει μόνο το τελευταίο - αλλιώς ένα
    σφάλμα στο user_history 'σκέπαζε' τυχόν προηγούμενο σφάλμα στο user_errors
    και δεν το βλέπαμε ποτέ."""
    errors = st.session_state.get("sync_errors", [])
    if msg not in errors:
        errors.append(msg)
    st.session_state["sync_errors"] = errors

def log_notification_attempt(event_type, detail, success):
    """
    Καταγράφει ΜΟΝΙΜΑ (στη βάση, όχι σε session_state) κάθε απόπειρα
    αποστολής admin notification email - πετυχημένη ή όχι. Έτσι μπορούμε να
    ελέγξουμε οποτεδήποτε μέσω SQL Editor τι συνέβη, ανεξάρτητα από το ποιο
    browser/session/συσκευή χρησιμοποίησε ο πελάτης μετά. Χρησιμοποιεί το
    service_role key ώστε να δουλεύει ακόμα και πριν ο χρήστης έχει καν
    συνδεθεί (π.χ. τη στιγμή της εγγραφής).
    """
    if not SERVICE_ROLE_KEY:
        return
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}", "Content-Type": "application/json"}
    try:
        httpx.post(
            f"{URL}/rest/v1/notification_log", headers=headers,
            json={"event_type": event_type, "detail": detail, "success": success},
            timeout=8,
        )
    except Exception:
        pass  # ακόμα και αυτό το logging είναι best-effort - ποτέ δεν μπλοκάρει τίποτα

def send_admin_notification(subject, body_html, event_type=None):
    """
    Στέλνει email ειδοποίησης στον ADMIN_EMAIL μέσω Resend (π.χ. νέα εγγραφή,
    αίτημα Premium). Ποτέ δεν μπλοκάρει το UI. Κάθε απόπειρα καταγράφεται
    ΜΟΝΙΜΑ στη βάση (log_notification_attempt), ανεξάρτητα από session -
    αυτό είναι το αξιόπιστο σημείο ελέγχου, όχι το sidebar warning.
    """
    event_type = event_type or subject
    if not RESEND_API_KEY:
        record_sync_error("send_admin_notification: λείπει το RESEND_API_KEY")
        log_notification_attempt(event_type, "Λείπει το RESEND_API_KEY", False)
        return False
    if not ADMIN_EMAIL:
        record_sync_error("send_admin_notification: λείπει το ADMIN_EMAIL")
        log_notification_attempt(event_type, "Λείπει το ADMIN_EMAIL", False)
        return False
    try:
        res = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "ASEP Study <notifications@asepstudy.gr>",
                "to": [ADMIN_EMAIL],
                "subject": subject,
                "html": body_html,
            },
            timeout=10,
        )
        ok = res.status_code in (200, 201)
        if not ok:
            detail = f"HTTP {res.status_code} - {res.text[:300]}"
            record_sync_error(f"send_admin_notification: {detail}")
            log_notification_attempt(event_type, detail, False)
        else:
            log_notification_attempt(event_type, "OK", True)
        return ok
    except Exception as e:
        record_sync_error(f"send_admin_notification: {e}")
        log_notification_attempt(event_type, str(e)[:300], False)
        return False

def load_user_errors(user_id, token, include_mastered=False):
    """
    Επιστρέφει dict {question_text: {...}} με πλήρη στοιχεία ανά ερώτηση:
    wrong_count, correct_streak, mastered, mastered_at_test_num.

    include_mastered=False (προεπιλογή): επιστρέφει ΜΟΝΟ τις ενεργές ερωτήσεις
    του μητρώου (mastered=false) - αυτές εμφανίζονται στο UI του Μητρώου Λαθών
    και μπαίνουν στην κλήρωση των 5 ερωτήσεων κάθε τεστ.

    include_mastered=True: επιστρέφει ΟΛΕΣ τις γραμμές (και τις mastered),
    ώστε να μπορούμε να ελέγξουμε αν κάποια "μαθημένη" ερώτηση πρέπει να
    ξαναεμφανιστεί για επαλήθευση μετά από 5-6 τεστ.
    """
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        params = {
            "user_id": f"eq.{user_id}",
            "select": "question_text,wrong_count,correct_streak,mastered,mastered_at_test_num",
        }
        if not include_mastered:
            params["mastered"] = "eq.false"
        res = httpx.get(f"{URL}/rest/v1/user_errors", headers=headers, params=params)
        res.raise_for_status()
        return {
            item["question_text"]: {
                "wrong_count": item.get("wrong_count", 1),
                "correct_streak": item.get("correct_streak", 0),
                "mastered": item.get("mastered", False),
                "mastered_at_test_num": item.get("mastered_at_test_num"),
            }
            for item in res.json()
        }
    except Exception as e:
        record_sync_error(f"load_user_errors: {e}")
        return {}

MASTERY_STREAK_REQUIRED = 3       # συνεχόμενες σωστές για να θεωρηθεί "μαθημένη"
MASTERY_RECHECK_AFTER_TESTS = 5   # μετά από πόσα τεστ την ξαναφέρνουμε για επαλήθευση

EVENT_LABELS = {
    "mastered": "🎓 Μπράβο! Τη έμαθες — βγήκε από το ενεργό μητρώο λαθών.",
    "graduated": "🏆 Πέρασες την επαλήθευση! Η ερώτηση αφαιρέθηκε οριστικά.",
    "resurfaced_fail": "⚠️ Ήταν σε επαλήθευση και τη ξανάκανες λάθος — επέστρεψε στο ενεργό μητρώο.",
    "streak_up": "🔥 Σωστό ξανά — μια ακόμα σωστή και τη μαθαίνεις!",
}

def sync_user_error(user_id, token, question_text, is_correct, entry, current_test_num):
    """
    Ενημερώνει το user_errors μετά από μια απάντηση, με πλήρη mastery λογική.

    entry: το dict {wrong_count, correct_streak, mastered, mastered_at_test_num}
           όπως το έχουμε ήδη φορτωμένο για αυτή την ερώτηση (None αν δεν υπάρχει
           καθόλου ακόμα στο μητρώο - πρώτη φορά που τη βλέπουμε ως λάθος).
    current_test_num: πόσο το συνολικό νούμερο τεστ που μόλις ολοκλήρωσε ο χρήστης
                       (μετά την προσθήκη αυτού του τεστ) - χρησιμοποιείται για να
                       θυμόμαστε "σε ποιο τεστ έμαθε" και πότε να την ξαναφέρουμε.
    Επιστρέφει: (ok: bool, event: str) όπου event in
                {"none","added","streak_up","mastered","reset","graduated","resurfaced_fail"}
    """
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        if entry is None:
            # Δεν υπήρχε καθόλου στο μητρώο πριν από αυτό το τεστ.
            if is_correct:
                return True, "none"  # σωστή απάντηση σε ερώτηση που δεν ήταν λάθος - τίποτα να κάνουμε
            res = httpx.post(
                f"{URL}/rest/v1/user_errors", headers=headers,
                json={
                    "user_id": user_id, "question_text": question_text,
                    "wrong_count": 1, "correct_streak": 0, "mastered": False,
                },
            )
            ok = res.status_code in (200, 201, 204, 409)
            if not ok:
                record_sync_error(f"sync_user_error(insert): HTTP {res.status_code} - {res.text[:200]}")
            return ok, "added"

        # --- Η ερώτηση ήδη υπάρχει στο μητρώο (mastered ή όχι) ---
        was_mastered = entry.get("mastered", False)

        if is_correct:
            new_streak = entry.get("correct_streak", 0) + 1

            if was_mastered:
                # Ήταν ήδη "μαθημένη" και μας ξαναήρθε για επαλήθευση (recheck).
                # Το πέτυχε -> αποφοιτεί οριστικά, διαγράφεται εντελώς.
                res = httpx.delete(
                    f"{URL}/rest/v1/user_errors", headers=headers,
                    params={"user_id": f"eq.{user_id}", "question_text": f"eq.{question_text}"},
                )
                ok = res.status_code in (200, 204)
                if not ok:
                    record_sync_error(f"sync_user_error(graduate): HTTP {res.status_code} - {res.text[:200]}")
                return ok, "graduated"

            if new_streak >= MASTERY_STREAK_REQUIRED:
                # Μόλις έφτασε στο όριο -> γίνεται "μαθημένη", βγαίνει από το
                # ενεργό μητρώο, αλλά ΔΕΝ διαγράφεται - μένει για να την
                # ξαναφέρουμε αργότερα ως recheck.
                res = httpx.patch(
                    f"{URL}/rest/v1/user_errors", headers=headers,
                    params={"user_id": f"eq.{user_id}", "question_text": f"eq.{question_text}"},
                    json={
                        "correct_streak": new_streak, "mastered": True,
                        "mastered_at_test_num": current_test_num,
                    },
                )
                ok = res.status_code in (200, 204)
                if not ok:
                    record_sync_error(f"sync_user_error(mastered): HTTP {res.status_code} - {res.text[:200]}")
                return ok, "mastered"

            # Σωστή, αλλά όχι ακόμα αρκετές συνεχόμενες φορές.
            res = httpx.patch(
                f"{URL}/rest/v1/user_errors", headers=headers,
                params={"user_id": f"eq.{user_id}", "question_text": f"eq.{question_text}"},
                json={"correct_streak": new_streak},
            )
            ok = res.status_code in (200, 204)
            if not ok:
                record_sync_error(f"sync_user_error(streak_up): HTTP {res.status_code} - {res.text[:200]}")
            return ok, "streak_up"

        else:
            # Λάθος απάντηση.
            if was_mastered:
                # Recheck απέτυχε -> επιστρέφει κανονικά στο ενεργό μητρώο,
                # σαν να μην την είχε μάθει ποτέ (μηδενισμός streak).
                res = httpx.patch(
                    f"{URL}/rest/v1/user_errors", headers=headers,
                    params={"user_id": f"eq.{user_id}", "question_text": f"eq.{question_text}"},
                    json={
                        "wrong_count": entry.get("wrong_count", 1) + 1,
                        "correct_streak": 0, "mastered": False,
                        "mastered_at_test_num": None,
                    },
                )
                ok = res.status_code in (200, 204)
                if not ok:
                    record_sync_error(f"sync_user_error(resurfaced_fail): HTTP {res.status_code} - {res.text[:200]}")
                return ok, "resurfaced_fail"

            # Απλό λάθος σε ενεργή ερώτηση του μητρώου -> μηδενισμός streak,
            # αύξηση wrong_count.
            res = httpx.patch(
                f"{URL}/rest/v1/user_errors", headers=headers,
                params={"user_id": f"eq.{user_id}", "question_text": f"eq.{question_text}"},
                json={"wrong_count": entry.get("wrong_count", 1) + 1, "correct_streak": 0},
            )
            ok = res.status_code in (200, 204)
            if not ok:
                record_sync_error(f"sync_user_error(reset): HTTP {res.status_code} - {res.text[:200]}")
            return ok, "reset"

    except Exception as e:
        record_sync_error(f"sync_user_error: {e}")
        return False, "none"

def load_user_history(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/user_history", headers=headers,
            params={"user_id": f"eq.{user_id}", "select": "score,percentage,created_at", "order": "created_at.desc"},
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        record_sync_error(f"load_user_history: {e}")
        return []

def save_user_history(user_id, token, score, percentage):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        res = httpx.post(
            f"{URL}/rest/v1/user_history", headers=headers,
            json={"user_id": user_id, "score": score, "percentage": percentage},
        )
        ok = res.status_code in (200, 201)
        if not ok:
            record_sync_error(f"save_user_history: HTTP {res.status_code} - {res.text[:200]}")
        return ok
    except Exception as e:
        record_sync_error(f"save_user_history: {e}")
        return False

# --- QUIZ PROGRESS AUTO-SAVE ---
# Χρειάζεται τον πίνακα quiz_progress (βλ. οδηγίες SQL). Κάθε φορά που ο
# χρήστης αλλάζει σελίδα/απάντηση μέσα σε ΕΠΙΣΗΜΟ τεστ, αποθηκεύουμε την
# τρέχουσα κατάσταση (ερωτήσεις, απαντήσεις, σελίδα, ώρα έναρξης) ώστε αν
# χαθεί η σύνδεση / κλείσει ο browser, να μπορεί να συνεχίσει από εκεί.
def save_quiz_progress(user_id, token, quiz, answers, current_page, start_time):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        payload = {
            "user_id": user_id,
            "quiz_data": json.dumps(quiz),
            "answers": json.dumps(answers),
            "current_page": current_page,
            "start_time": start_time,
        }
        res = httpx.post(
            f"{URL}/rest/v1/quiz_progress", headers=headers,
            params={"on_conflict": "user_id"},
            json=payload,
        )
        # Prefer: resolution=merge-duplicates θα έπρεπε ιδανικά να είναι στο header,
        # αλλά το on_conflict param από μόνο του αρκεί με unique constraint στο user_id
        # + "Prefer: resolution=merge-duplicates" header:
        if res.status_code not in (200, 201):
            headers2 = {**headers, "Prefer": "resolution=merge-duplicates"}
            res = httpx.post(
                f"{URL}/rest/v1/quiz_progress", headers=headers2,
                params={"on_conflict": "user_id"}, json=payload,
            )
        return res.status_code in (200, 201)
    except Exception as e:
        record_sync_error(f"save_quiz_progress: {e}")
        return False

def load_quiz_progress(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/quiz_progress", headers=headers,
            params={"user_id": f"eq.{user_id}", "select": "*"},
        )
        res.raise_for_status()
        rows = res.json()
        if not rows:
            return None
        row = rows[0]
        return {
            "quiz": json.loads(row["quiz_data"]),
            "answers": {int(k): v for k, v in json.loads(row["answers"]).items()},
            "current_page": row["current_page"],
            "start_time": row["start_time"],
        }
    except Exception as e:
        record_sync_error(f"load_quiz_progress: {e}")
        return None

def delete_quiz_progress(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        httpx.delete(
            f"{URL}/rest/v1/quiz_progress", headers=headers,
            params={"user_id": f"eq.{user_id}"},
        )
    except Exception:
        pass  # όχι κρίσιμο - δεν μπλοκάρουμε το UI αν αποτύχει το cleanup

# --- ΚΑΛΥΨΗ ΧΩΡΙΣ ΕΠΑΝΑΛΗΨΗ (question_cycle) ---
# Θυμόμαστε ποιες "κανονικές" ερωτήσεις έχει ήδη δει ο χρήστης στον τρέχοντα
# "κύκλο", ώστε κάθε τεστ να τραβάει κατά προτεραιότητα από τις αχρησιμοποίητες.
# Όταν εξαντληθούν, ανοίγει νέος κύκλος (μηδενισμός + cycle_number += 1).
def load_question_cycle(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/question_cycle", headers=headers,
            params={"user_id": f"eq.{user_id}", "select": "*"},
        )
        res.raise_for_status()
        rows = res.json()
        if not rows:
            return {"seen": set(), "cycle": 1}
        row = rows[0]
        return {"seen": set(row.get("seen_questions") or []), "cycle": row.get("cycle_number", 1)}
    except Exception as e:
        record_sync_error(f"load_question_cycle: {e}")
        return {"seen": set(), "cycle": 1}

def save_question_cycle(user_id, token, seen_set, cycle_number):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        payload = {
            "user_id": user_id,
            "seen_questions": list(seen_set),
            "cycle_number": cycle_number,
        }
        res = httpx.post(
            f"{URL}/rest/v1/question_cycle", headers=headers,
            params={"on_conflict": "user_id"}, json=payload,
        )
        if res.status_code not in (200, 201):
            headers2 = {**headers, "Prefer": "resolution=merge-duplicates"}
            res = httpx.post(
                f"{URL}/rest/v1/question_cycle", headers=headers2,
                params={"on_conflict": "user_id"}, json=payload,
            )
        ok = res.status_code in (200, 201)
        if not ok:
            record_sync_error(f"save_question_cycle: HTTP {res.status_code} - {res.text[:200]}")
        return ok
    except Exception as e:
        record_sync_error(f"save_question_cycle: {e}")
        return False

def pick_normal_questions(normal_pool, needed_count, seen_set, cycle_number):
    """
    Επιλέγει 'needed_count' ερωτήσεις από το normal_pool, με προτεραιότητα
    σε αυτές που ΔΕΝ έχουν ξαναδοθεί στον τρέχοντα κύκλο. Αν δεν επαρκούν οι
    αχρησιμοποίητες, κλείνει τον κύκλο (reset) και συμπληρώνει τις υπόλοιπες
    από νέο κύκλο. Επιστρέφει (chosen_questions, new_seen_set, new_cycle_number).
    """
    unseen = [q for q in normal_pool if q["question"] not in seen_set]

    if len(unseen) >= needed_count:
        chosen = random.sample(unseen, needed_count)
        new_seen = set(seen_set)
        new_seen.update(q["question"] for q in chosen)
        return chosen, new_seen, cycle_number

    # Δεν αρκούν οι αχρησιμοποίητες -> παίρνουμε όλες, ανοίγουμε νέο κύκλο,
    # και συμπληρώνουμε τις υπόλοιπες από τον νέο (καθαρό) κύκλο.
    chosen = list(unseen)
    remaining_needed = needed_count - len(chosen)
    already_chosen_texts = {q["question"] for q in chosen}
    refill_pool = [q for q in normal_pool if q["question"] not in already_chosen_texts]
    refill = random.sample(refill_pool, min(remaining_needed, len(refill_pool))) if refill_pool else []
    chosen += refill

    new_cycle_number = cycle_number + 1
    new_seen = {q["question"] for q in chosen}  # ο νέος κύκλος ξεκινάει με ό,τι μόλις δόθηκε
    return chosen, new_seen, new_cycle_number

TERMS_AND_PRIVACY_TEXT = """
**ΣΗΜΕΙΩΣΗ:** Αυτό είναι ένα βασικό, εναρκτήριο κείμενο και ΔΕΝ έχει ελεγχθεί
από δικηγόρο. Πρέπει να αντικατασταθεί με νομικά εγκεκριμένο κείμενο πριν
την πλήρη εμπορική λειτουργία (πραγματικές πληρωμές).

### Όροι Χρήσης

Χρησιμοποιώντας τον "Έξυπνο Προσομοιωτή Διαγωνισμού ΑΣΕΠ" ("η Υπηρεσία"),
αποδέχεσαι τα παρακάτω:

1. Η Υπηρεσία παρέχεται "ως έχει", χωρίς εγγύηση ακρίβειας των ερωτήσεων ή
   καταλληλότητας για συγκεκριμένο διαγωνισμό ΑΣΕΠ.
2. Ο λογαριασμός σου είναι προσωπικός - δεν επιτρέπεται η κοινή χρήση
   στοιχείων σύνδεσης.
3. Επιφυλασσόμαστε του δικαιώματος αναστολής λογαριασμού σε περίπτωση
   κατάχρησης ή παραβίασης αυτών των όρων.
4. Το δωρεάν επίπεδο χρήσης έχει συγκεκριμένα όρια (αριθμός τεστ/εξασκήσεων)
   όπως περιγράφονται στην εφαρμογή, τα οποία μπορούν να αλλάξουν.

### Πολιτική Απορρήτου

**Ποια δεδομένα συλλέγουμε:** email διεύθυνση (για τον λογαριασμό σου),
τις απαντήσεις σου σε τεστ, και στατιστικά προόδου (σκορ, ιστορικό λαθών).

**Γιατί:** αποκλειστικά για να λειτουργήσουν οι βασικές λειτουργίες της
εφαρμογής (μητρώο λαθών, ιστορικό επιδόσεων, προσωποποιημένη εξάσκηση).

**Πού αποθηκεύονται:** σε βάση δεδομένων τρίτου παρόχου (Supabase).

**Δεν πουλάμε ή μοιραζόμαστε** τα δεδομένα σου με τρίτους για διαφημιστικούς
σκοπούς.

**Τα δικαιώματά σου:** μπορείς να ζητήσεις πρόσβαση, διόρθωση, ή πλήρη
διαγραφή των δεδομένων σου οποιαδήποτε στιγμή, επικοινωνώντας μαζί μας.

**Πληρωμές:** αυτή τη στιγμή η Υπηρεσία δεν επεξεργάζεται πληρωμές (μόνο
δήλωση ενδιαφέροντος). Όταν ενεργοποιηθούν πληρωμές, θα ενημερωθεί αυτό το
κείμενο ανάλογα.

**Επικοινωνία:** [συμπλήρωσε το email επικοινωνίας σου εδώ]

_Τελευταία ενημέρωση: {}_
""".format(datetime.utcnow().strftime("%d/%m/%Y"))

# --- FREEMIUM GATING (profiles) ---
FREE_OFFICIAL_TESTS = 2       # δωρεάν επίσημα τεστ συνολικά
FREE_QUICK_TESTS = 1          # δωρεάν Γρήγορο Τεστ Μητρώου συνολικά
# Εξάσκηση ανά Ενότητα: 1 δωρεάν φορά ΑΝΑ ενότητα (παρακολουθείται στη λίστα sections_practiced)

def get_client_user_agent():
    """
    Διαβάζει το User-Agent header του request, αν το τρέχον Streamlit
    υποστηρίζει st.context.headers (νεότερες εκδόσεις). Αν όχι διαθέσιμο,
    επιστρέφει 'Άγνωστο' χωρίς να ρίξει exception - καθαρά best-effort.
    """
    try:
        return st.context.headers.get("User-Agent", "Άγνωστο")
    except Exception:
        return "Άγνωστο"

def guess_device_type(user_agent):
    """Πολύ απλή ευρετική εκτίμηση Mobile/Tablet/Desktop από το User-Agent string."""
    if not user_agent or user_agent == "Άγνωστο":
        return "Άγνωστο"
    ua = user_agent.lower()
    if "ipad" in ua or "tablet" in ua:
        return "📱 Tablet"
    if "mobi" in ua or "android" in ua or "iphone" in ua:
        return "📱 Κινητό"
    return "💻 Υπολογιστής"

def load_or_create_profile(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{user_id}", "select": "*"},
        )
        res.raise_for_status()
        rows = res.json()
        if rows:
            row = rows[0]
            return {
                "is_premium": row.get("is_premium", False),
                "official_tests_used": row.get("official_tests_used", 0),
                "quick_test_used": row.get("quick_test_used", False),
                "sections_practiced": set(row.get("sections_practiced") or []),
                "premium_interest": row.get("premium_interest", False),
            }
        # Δεν υπάρχει ακόμα προφίλ -> δημιουργούμε ένα με τα defaults,
        # καταγράφοντας και το User-Agent της πρώτης φοράς που φορτώνει προφίλ
        # (πρακτικά, η πρώτη πραγματική σύνδεση/χρήση του λογαριασμού).
        headers_post = {**headers, "Content-Type": "application/json"}
        httpx.post(
            f"{URL}/rest/v1/profiles", headers=headers_post,
            json={"user_id": user_id, "signup_user_agent": get_client_user_agent()},
        )
        return {
            "is_premium": False, "official_tests_used": 0,
            "quick_test_used": False, "sections_practiced": set(),
            "premium_interest": False,
        }
    except Exception as e:
        record_sync_error(f"load_or_create_profile: {e}")
        # Fail-safe: αν αποτύχει το φόρτωμα, ΔΕΝ δίνουμε premium πρόσβαση από λάθος -
        # απλά εμφανίζουμε 0 χρήσεις (ο χρήστης βλέπει τα όριά του κανονικά).
        return {
            "is_premium": False, "official_tests_used": 0,
            "quick_test_used": False, "sections_practiced": set(),
            "premium_interest": False,
        }

def increment_official_tests_used(user_id, token, new_count):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        res = httpx.patch(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{user_id}"},
            json={"official_tests_used": new_count},
        )
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"increment_official_tests_used: {e}")
        return False

def mark_quick_test_used(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        res = httpx.patch(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{user_id}"},
            json={"quick_test_used": True},
        )
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"mark_quick_test_used: {e}")
        return False

def mark_section_practiced(user_id, token, section, current_sections_set):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        new_set = set(current_sections_set)
        new_set.add(section)
        res = httpx.patch(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{user_id}"},
            json={"sections_practiced": list(new_set)},
        )
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"mark_section_practiced: {e}")
        return False

def mark_premium_interest(user_id, token):
    headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        res = httpx.patch(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{user_id}"},
            json={"premium_interest": True},
        )
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"mark_premium_interest: {e}")
        return False

# --- ADMIN (χρησιμοποιεί το SERVICE_ROLE_KEY, ΟΧΙ το token του χρήστη) ---
# ΣΗΜΑΝΤΙΚΟ: αυτές οι συναρτήσεις καλούνται ΜΟΝΟ αν το email του συνδεδεμένου
# χρήστη ταιριάζει με το ADMIN_EMAIL (έλεγχος γίνεται στο UI πριν την κλήση).
# Το service_role key παρακάμπτει ΚΑΘΕ RLS/column-grant περιορισμό - γι' αυτό
# ποτέ δεν το βάζουμε σε headers που θα μπορούσε να δει/καλέσει απλός χρήστης.

def admin_list_all_users_basic():
    """Επιστρέφει dict {user_id: email} για όλους τους χρήστες, μέσω Admin API."""
    if not SERVICE_ROLE_KEY:
        return {}
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        res = httpx.get(f"{URL}/auth/v1/admin/users", headers=headers, params={"per_page": 1000})
        res.raise_for_status()
        users = res.json().get("users", [])
        return {u["id"]: u.get("email", "") for u in users}
    except Exception as e:
        record_sync_error(f"admin_list_all_users_basic: {e}")
        return {}

def admin_list_all_users_full():
    """
    Πλήρης λίστα όλων των χρηστών, συνδυάζοντας:
    - auth.users (μέσω Admin API): email, ημερομηνία εγγραφής, τελευταία
      σύνδεση, κατάσταση ban
    - profiles: is_premium, χρήσεις δωρεάν ορίων, premium_interest
    Επιστρέφει λίστα από dicts, ταξινομημένη με πιο πρόσφατη εγγραφή πρώτα.
    """
    if not SERVICE_ROLE_KEY:
        return []
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        auth_res = httpx.get(f"{URL}/auth/v1/admin/users", headers=headers, params={"per_page": 1000})
        auth_res.raise_for_status()
        auth_users = auth_res.json().get("users", [])

        prof_res = httpx.get(f"{URL}/rest/v1/profiles", headers=headers, params={"select": "*"})
        prof_res.raise_for_status()
        profiles_by_id = {p["user_id"]: p for p in prof_res.json()}

        combined = []
        for u in auth_users:
            uid = u["id"]
            prof = profiles_by_id.get(uid, {})
            banned_until = u.get("banned_until")
            is_banned = bool(banned_until) and banned_until != "none"
            combined.append({
                "user_id": uid,
                "email": u.get("email", "(άγνωστο)"),
                "created_at": u.get("created_at", ""),
                "last_sign_in_at": u.get("last_sign_in_at") or "-",
                "is_premium": prof.get("is_premium", False),
                "official_tests_used": prof.get("official_tests_used", 0),
                "quick_test_used": prof.get("quick_test_used", False),
                "sections_practiced": len(prof.get("sections_practiced") or []),
                "premium_interest": prof.get("premium_interest", False),
                "is_banned": is_banned,
                "user_agent": prof.get("signup_user_agent") or "Άγνωστο",
            })
        combined.sort(key=lambda x: x["created_at"], reverse=True)
        return combined
    except Exception as e:
        record_sync_error(f"admin_list_all_users_full: {e}")
        return []

def admin_ban_user(target_user_id, ban: bool):
    """Κάνει ban (ban=True) ή αναιρεί το ban (ban=False) ενός χρήστη μέσω Admin API."""
    if not SERVICE_ROLE_KEY:
        return False
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}", "Content-Type": "application/json"}
    try:
        payload = {"ban_duration": "87600h"} if ban else {"ban_duration": "none"}
        res = httpx.put(f"{URL}/auth/v1/admin/users/{target_user_id}", headers=headers, json=payload)
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"admin_ban_user: {e}")
        return False

def admin_delete_user_completely(target_user_id):
    """
    Διαγράφει ΟΡΙΣΤΙΚΑ έναν χρήστη: πρώτα όλα τα σχετικά δεδομένα του στους
    δικούς μας πίνακες (γιατί δεν έχουμε ON DELETE CASCADE στα foreign keys),
    και μετά τον ίδιο τον λογαριασμό μέσω Admin API. Μη αναστρέψιμο.
    """
    if not SERVICE_ROLE_KEY:
        return False
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        for table in ("user_errors", "user_history", "quiz_progress", "question_cycle", "profiles"):
            httpx.delete(
                f"{URL}/rest/v1/{table}", headers=headers,
                params={"user_id": f"eq.{target_user_id}"},
            )
        res = httpx.delete(f"{URL}/auth/v1/admin/users/{target_user_id}", headers=headers)
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"admin_delete_user_completely: {e}")
        return False

def admin_list_pending_premium_requests():
    """Προφίλ με premium_interest=true & is_premium=false, μαζί με το email τους."""
    if not SERVICE_ROLE_KEY:
        return []
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"premium_interest": "eq.true", "is_premium": "eq.false", "select": "user_id"},
        )
        res.raise_for_status()
        rows = res.json()
        emails = admin_list_all_users_basic()
        return [{"user_id": r["user_id"], "email": emails.get(r["user_id"], "(άγνωστο email)")} for r in rows]
    except Exception as e:
        record_sync_error(f"admin_list_pending_premium_requests: {e}")
        return []

def admin_list_all_premium():
    """Όλοι οι χρήστες με is_premium=true, μαζί με το email τους."""
    if not SERVICE_ROLE_KEY:
        return []
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"is_premium": "eq.true", "select": "user_id"},
        )
        res.raise_for_status()
        rows = res.json()
        emails = admin_list_all_users_basic()
        return [{"user_id": r["user_id"], "email": emails.get(r["user_id"], "(άγνωστο email)")} for r in rows]
    except Exception as e:
        record_sync_error(f"admin_list_all_premium: {e}")
        return []

def admin_set_premium(target_user_id, value: bool):
    """Εγκρίνει (True) ή αναιρεί (False) το Premium ενός χρήστη. Απαιτεί service_role key."""
    if not SERVICE_ROLE_KEY:
        return False
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}", "Content-Type": "application/json"}
    try:
        res = httpx.patch(
            f"{URL}/rest/v1/profiles", headers=headers,
            params={"user_id": f"eq.{target_user_id}"},
            json={"is_premium": value},
        )
        return res.status_code in (200, 204)
    except Exception as e:
        record_sync_error(f"admin_set_premium: {e}")
        return False

def admin_list_recent_notifications(limit=20):
    """Τα πιο πρόσφατα notification_log entries - ανεξάρτητα από session, πάντα διαθέσιμα."""
    if not SERVICE_ROLE_KEY:
        return []
    headers = {"apikey": SERVICE_ROLE_KEY, "Authorization": f"Bearer {SERVICE_ROLE_KEY}"}
    try:
        res = httpx.get(
            f"{URL}/rest/v1/notification_log", headers=headers,
            params={"select": "*", "order": "created_at.desc", "limit": limit},
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        record_sync_error(f"admin_list_recent_notifications: {e}")
        return []

# --- 4. APP UI ---
if "auth" not in st.session_state: st.session_state["auth"] = None

# --- ΔΙΑΧΕΙΡΙΣΗ RECOVERY TOKEN (μετά από κλικ σε link επαναφοράς κωδικού) ---
# Έχει προτεραιότητα έναντι όλων - αν ο χρήστης μόλις ήρθε από email reset,
# δείχνουμε ΜΟΝΟ τη φόρμα "Όρισε νέο κωδικό", ανεξάρτητα αν είναι ήδη
# συνδεδεμένος ή όχι.
_recovery_token = st.query_params.get("recovery_token")
if _recovery_token:
    st.subheader("🔑 Ορισμός Νέου Κωδικού")
    new_pass = st.text_input("Νέος κωδικός (τουλάχιστον 6 χαρακτήρες)", type="password", key="recovery_new_pass")
    new_pass_confirm = st.text_input("Επιβεβαίωση νέου κωδικού", type="password", key="recovery_new_pass_confirm")
    if st.button("✅ Αποθήκευση Νέου Κωδικού", type="primary"):
        if not new_pass or len(new_pass) < 6:
            st.warning("Ο κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες.")
        elif new_pass != new_pass_confirm:
            st.warning("Οι κωδικοί δεν ταιριάζουν.")
        else:
            ok, err = supabase_update_password(_recovery_token, new_pass)
            if ok:
                st.success("🎉 Ο κωδικός άλλαξε επιτυχώς! Μπορείς τώρα να συνδεθείς.")
                st.query_params.clear()
                if st.button("Μετάβαση στη Σύνδεση"):
                    st.rerun()
            else:
                st.error(err)
    st.stop()

if st.session_state["auth"] is None:
    st.subheader("🔐 Σύνδεση Υποψηφίου")
    auth_mode = st.tabs(["Είσοδος Χρήστη", "Δημιουργία Λογαριασμού"])
    with auth_mode[0]:
        email = st.text_input("Email", key="l_email")
        password = st.text_input("Κωδικός", type="password", key="l_pass")
        if st.button("Είσοδος"):
            if not email or not password:
                st.warning("Συμπλήρωσε email και κωδικό.")
            else:
                session, err = supabase_login(email, password)
                if session:
                    st.session_state["auth"] = session; st.rerun()
                else:
                    st.error(err)

        with st.expander("🔑 Ξέχασα τον κωδικό μου"):
            forgot_email = st.text_input("Email λογαριασμού", key="forgot_email")
            if st.button("📧 Στείλε μου link επαναφοράς"):
                if not forgot_email:
                    st.warning("Συμπλήρωσε το email σου.")
                else:
                    ok, err = supabase_request_password_reset(forgot_email, None)
                    if ok:
                        st.success(
                            "📧 Αν το email υπάρχει στο σύστημά μας, θα λάβεις σύνδεσμο "
                            "επαναφοράς κωδικού. Έλεγξε τα εισερχόμενα (και τα spam)."
                        )
                    else:
                        st.error(err)
    with auth_mode[1]:
        s_email = st.text_input("Email", key="s_email")
        s_pass = st.text_input("Κωδικός", type="password", key="s_pass")
        agree_terms = st.checkbox(
            "Αποδέχομαι τους Όρους Χρήσης και την Πολιτική Απορρήτου",
            key="agree_terms",
        )
        with st.expander("Διάβασε τους Όρους Χρήσης & Πολιτική Απορρήτου"):
            st.markdown(TERMS_AND_PRIVACY_TEXT)
        if st.button("Εγγραφή"):
            if not s_email or not s_pass:
                st.warning("Συμπλήρωσε email και κωδικό.")
            elif not agree_terms:
                st.warning("Πρέπει να αποδεχτείς τους Όρους Χρήσης για να εγγραφείς.")
            else:
                ok, err = supabase_signup(s_email, s_pass)
                if ok:
                    st.success("🎉 Επιτυχία! Ελέγξε το email σου για επιβεβαίωση, μετά συνδέσου.")
                    send_admin_notification(
                        "🆕 Νέα εγγραφή στο ASEP Study",
                        f"<p>Νέος χρήστης εγγράφηκε:</p><p><b>{s_email}</b></p>",
                    )
                else:
                    st.error(err)
else:
    user_id = st.session_state["auth"]["user_id"]
    token = st.session_state["auth"]["token"]
    all_questions = load_questions()

    # Μία μόνο κλήση δικτύου: φέρνουμε ΟΛΕΣ τις γραμμές (ενεργές + mastered),
    # και τις χωρίζουμε τοπικά. wrong_history = ενεργές λάθος ερωτήσεις (αυτές
    # που μπαίνουν στην κλήρωση/εμφανίζονται στο Μητρώο Λαθών).
    # mastered_history = "μαθημένες" ερωτήσεις (κρατάμε για recheck logic).
    _all_errors = load_user_errors(user_id, token, include_mastered=True)
    wrong_history = {q: e for q, e in _all_errors.items() if not e["mastered"]}
    mastered_history = {q: e for q, e in _all_errors.items() if e["mastered"]}
    test_history = load_user_history(user_id, token)
    profile = load_or_create_profile(user_id, token)
    is_premium = profile["is_premium"]

    # Έλεγχος αν υπάρχει αποθηκευμένη πρόοδος ημιτελούς τεστ (auto-save).
    # Το κάνουμε μόνο μία φορά ανά "νέα" σύνδεση (και όχι αν το τεστ τρέχει
    # ήδη σε αυτό το session), για να μην κάνουμε άσκοπο δίκτυο σε κάθε rerun.
    if "_has_saved_progress" not in st.session_state and not st.session_state.get("quiz_started"):
        _progress = load_quiz_progress(user_id, token)
        st.session_state["_has_saved_progress"] = _progress is not None

    st.sidebar.markdown("## 🎯 ASEP Simulator")
    st.sidebar.caption("Έξυπνος Προσομοιωτής Διαγωνισμού")
    if is_premium:
        st.sidebar.success("⭐ Premium λογαριασμός")
    else:
        st.sidebar.info(
            f"🆓 Δωρεάν λογαριασμός\n\n"
            f"Τεστ: {profile['official_tests_used']}/{FREE_OFFICIAL_TESTS} · "
            f"Γρήγορο Τεστ: {'1' if profile['quick_test_used'] else '0'}/{FREE_QUICK_TESTS}"
        )
    st.sidebar.divider()
    st.sidebar.header("📊 Η Πρόοδός Σου (Cloud)")
    st.sidebar.metric("🟥 Ερωτήσεις στο Μητρώο Λαθών", len(wrong_history))
    st.sidebar.metric("📝 Συνολικά Τεστ", len(test_history))
    if st.session_state.get("sync_errors"):
        with st.sidebar.expander(f"⚠️ {len(st.session_state['sync_errors'])} προβλήματα συγχρονισμού", expanded=True):
            for err in st.session_state["sync_errors"]:
                st.write(err)
        if st.sidebar.button("Απόκρυψη προειδοποιήσεων"):
            st.session_state["sync_errors"] = []; st.rerun()

    # State-driven navigation αντί για sidebar radio: οι επιλογές γίνονται τώρα
    # από τις κάρτες στον προθάλαμο. Το sidebar radio αντικαθίσταται από ένα
    # απλό "Αρχική" κουμπί που επιστρέφει στον προθάλαμο.
    if "app_mode" not in st.session_state:
        st.session_state["app_mode"] = "🏛️ Προθάλαμος"

    st.sidebar.divider()
    if st.sidebar.button("🏛️ Αρχική (Προθάλαμος)", use_container_width=True):
        st.session_state["app_mode"] = "🏛️ Προθάλαμος"
        if "quiz_started" in st.session_state: st.session_state["quiz_started"] = False
        st.rerun()

    if st.sidebar.button("ℹ️ Πώς λειτουργεί η εφαρμογή", use_container_width=True):
        st.session_state["app_mode"] = "ℹ️ Οδηγός Χρήσης"
        st.rerun()

    if st.sidebar.button("📜 Όροι & Απόρρητο", use_container_width=True):
        st.session_state["app_mode"] = "📜 Όροι & Απόρρητο"
        st.rerun()

    _is_admin = ADMIN_EMAIL and st.session_state["auth"].get("email") == ADMIN_EMAIL
    if _is_admin:
        if st.sidebar.button("🛠️ Διαχειριστικό (Admin)", use_container_width=True):
            st.session_state["app_mode"] = "🛠️ Admin Panel"
            st.rerun()

    if st.sidebar.button("🚪 Αποσύνδεση", use_container_width=True):
        st.session_state["auth"] = None
        for k in ("quiz_started", "app_mode"):
            if k in st.session_state: del st.session_state[k]
        st.rerun()

    app_mode = st.session_state["app_mode"]
    _prev_mode = st.session_state.get("_prev_app_mode")
    st.session_state["_prev_app_mode"] = app_mode
    just_entered_study = (app_mode == "📖 Μελέτη Μητρώου Λαθών" and _prev_mode != app_mode)
    if just_entered_study:
        for k in list(st.session_state.keys()):
            if k.startswith("study_answer::") or k.startswith("study_checked::") or k.startswith("radio::"):
                del st.session_state[k]

    # ------------------------------------------------------------------
    # ΠΡΟΘΑΛΑΜΟΣ - dashboard-style με 3 μεγάλες κάρτες
    # ------------------------------------------------------------------
    if app_mode == "🏛️ Προθάλαμος":
        st.title("🏛️ Καλώς ήρθες!")
        st.markdown("Επίλεξε μία από τις παρακάτω λειτουργίες:")

        # --- Δείκτης κάλυψης ύλης (no-repeat cycle) ---
        _cycle_info = load_question_cycle(user_id, token)
        _total_q = len(all_questions)
        _seen_count = len(_cycle_info["seen"])
        if _total_q > 0:
            st.progress(
                min(_seen_count / _total_q, 1.0),
                text=f"📚 Κύκλος #{_cycle_info['cycle']} — έχεις δει {_seen_count}/{_total_q} μοναδικές ερωτήσεις",
            )
        st.write("")

        col1, col2, col3 = st.columns(3)

        with col1:
            with st.container(border=True):
                st.markdown("### 📝 Τεστ Προσομοίωσης")
                st.caption("25 ερωτήσεις • 25 λεπτά")
                st.markdown(
                    "Επίσημο τεστ με **έως 2 ερωτήσεις** από το μητρώο λαθών σου "
                    "και **17 νέες** ερωτήσεις, με χρονόμετρο 25 λεπτών."
                )
                st.write("")
                _test_limit_reached = (not is_premium) and profile["official_tests_used"] >= FREE_OFFICIAL_TESTS
                if _test_limit_reached:
                    st.caption(f"🔒 Έφτασες το όριο των {FREE_OFFICIAL_TESTS} δωρεάν τεστ.")
                if st.button(
                    "🚀 Έναρξη Τεστ", use_container_width=True, type="primary",
                    key="go_test", disabled=_test_limit_reached,
                ):
                    st.session_state["app_mode"] = "📝 Τεστ Προσομοίωσης"
                    st.rerun()

        with col2:
            with st.container(border=True):
                st.markdown("### 📖 Μητρώο Λαθών")
                st.caption(f"{len(wrong_history)} ερωτήσεις προς μελέτη")
                st.markdown(
                    "Δες όλες τις ερωτήσεις που έχεις απαντήσει λάθος, "
                    "**ομαδοποιημένες ανά θεματική ενότητα**, με τη σωστή απάντηση."
                )
                st.write("")
                if st.button("📖 Άνοιγμα Μητρώου", use_container_width=True, key="go_wrong"):
                    st.session_state["app_mode"] = "📖 Μελέτη Μητρώου Λαθών"
                    st.rerun()

        with col3:
            with st.container(border=True):
                st.markdown("### 📊 Ιστορικό Επιδόσεων")
                st.caption(f"{len(test_history)} ολοκληρωμένα τεστ")
                st.markdown(
                    "Δες τη **πρόοδό σου στον χρόνο**, στατιστικά επιδόσεων "
                    "και αναλυτικό πίνακα με όλα τα τεστ που έχεις κάνει."
                )
                st.write("")
                if st.button("📊 Άνοιγμα Ιστορικού", use_container_width=True, key="go_history"):
                    st.session_state["app_mode"] = "📊 Ιστορικό Επιδόσεων"
                    st.rerun()

        st.write("")
        st.divider()

        # --- Δήλωση ενδιαφέροντος για Premium (waitlist - καμία πληρωμή ακόμα) ---
        if not is_premium:
            with st.container(border=True):
                st.markdown("### ⭐ Premium — 69€ εφάπαξ (σύντομα διαθέσιμο)")
                st.markdown(
                    "- ♾️ Απεριόριστα Επίσημα Τεστ (αντί για 2 δωρεάν)\n"
                    "- ♾️ Απεριόριστη Εξάσκηση σε όλες τις ενότητες\n"
                    "- ♾️ Απεριόριστα Γρήγορα Τεστ Μητρώου\n"
                    "- Μία εφάπαξ πληρωμή, καμία συνδρομή"
                )
                if profile["premium_interest"]:
                    st.success("✅ Έχεις δηλώσει ενδιαφέρον! Θα σου στείλουμε email μόλις ανοίξει το Premium.")
                else:
                    st.caption(
                        "Το Premium δεν είναι ακόμα διαθέσιμο για αγορά. Δήλωσε ενδιαφέρον "
                        "και θα σε ενημερώσουμε πρώτο/η μόλις ανοίξει."
                    )
                    if st.button("🔔 Δήλωσε Ενδιαφέρον για Premium", use_container_width=True, type="primary"):
                        if mark_premium_interest(user_id, token):
                            st.success("✅ Ευχαριστούμε! Θα σε ενημερώσουμε μόλις ανοίξει το Premium.")
                            _user_email = st.session_state["auth"].get("email", "(άγνωστο email)")
                            send_admin_notification(
                                "⭐ Νέο αίτημα Premium - ASEP Study",
                                f"<p>Ο χρήστης <b>{_user_email}</b> δήλωσε ενδιαφέρον για Premium.</p>"
                                f"<p>Μπες στο Admin Panel για να τον εγκρίνεις.</p>",
                            )
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Κάτι πήγε στραβά, δοκίμασε ξανά.")
            st.write("")
            st.divider()

        # --- Resume ημιτελούς τεστ (auto-save) ---
        if st.session_state.get("_has_saved_progress"):
            st.info("💾 Έχεις ένα ημιτελές επίσημο τεστ αποθηκευμένο.")
            rc1, rc2 = st.columns(2)
            if rc1.button("▶️ Συνέχεια Τεστ", use_container_width=True, type="primary"):
                st.session_state["app_mode"] = "📝 Τεστ Προσομοίωσης"
                st.session_state["_resume_requested"] = True
                st.rerun()
            if rc2.button("🗑️ Απόρριψη", use_container_width=True):
                delete_quiz_progress(user_id, token)
                st.session_state["_has_saved_progress"] = False
                st.rerun()
            st.divider()

        # --- Γρήγορη Εξάσκηση ανά Ενότητα ---
        st.markdown("### 🎯 Γρήγορη Εξάσκηση ανά Ενότητα")
        st.caption("Εξάσκηση χωρίς χρονόμετρο, χωρίς επίδραση στο μητρώο λαθών ή στο ιστορικό.")
        all_sections = sorted(set((q.get("source") or "Γενικές Γνώσεις") for q in all_questions))
        if all_sections:
            pcol1, pcol2 = st.columns([3, 1])
            with pcol1:
                sel_section = st.selectbox("Διάλεξε ενότητα:", all_sections, key="practice_section_select")
            section_pool = [q for q in all_questions if (q.get("source") or "Γενικές Γνώσεις") == sel_section]
            _section_used = sel_section in profile["sections_practiced"]
            _section_locked = (not is_premium) and _section_used
            with pcol2:
                st.write("")
                st.write("")
                start_practice = st.button(
                    "🎯 Έναρξη", use_container_width=True, key="go_practice", disabled=_section_locked,
                )
            if _section_locked:
                st.caption(f"🔒 Έχεις ήδη κάνει τη δωρεάν εξάσκηση σε αυτή την ενότητα.")
            else:
                st.caption(f"{len(section_pool)} διαθέσιμες ερωτήσεις σε αυτή την ενότητα")
            if start_practice:
                pool = section_pool.copy()
                random.shuffle(pool)
                for k in list(st.session_state.keys()):
                    if k.startswith("practice_r_"):
                        del st.session_state[k]  # καθαρισμός stale radio-widget state από προηγούμενη εξάσκηση
                if not _section_used:
                    mark_section_practiced(user_id, token, sel_section, profile["sections_practiced"])
                st.session_state.update({
                    "practice_quiz": pool[:20], "practice_answers": {},
                    "practice_submitted": False, "practice_section": sel_section,
                })
                st.session_state["app_mode"] = "🎯 Εξάσκηση Ενότητας"
                st.rerun()

        st.write("")
        st.divider()

        # --- Γρήγορο Τεστ Μητρώου (10 ερωτήσεις, επηρεάζει κανονικά το mastery) ---
        st.markdown("### ⚡ Γρήγορο Τεστ Μητρώου Λαθών")
        st.caption(
            "Έως 10 ερωτήσεις **μόνο** από το ενεργό μητρώο λαθών σου, χωρίς χρονόμετρο. "
            "Οι σωστές απαντήσεις μετράνε κανονικά στο σύστημα μάθησης — 3 συνεχόμενες "
            "σωστές και η ερώτηση βγαίνει από το μητρώο. ΔΕΝ προστίθεται στο Ιστορικό Επιδόσεων."
        )
        if not wrong_history:
            st.info("🎉 Δεν έχεις καμία ερώτηση στο ενεργό μητρώο λαθών αυτή τη στιγμή.")
        else:
            _quick_locked = (not is_premium) and profile["quick_test_used"]
            if _quick_locked:
                st.caption("🔒 Έχεις ήδη χρησιμοποιήσει το δωρεάν Γρήγορο Τεστ.")
            else:
                st.caption(f"{len(wrong_history)} διαθέσιμες ερωτήσεις στο μητρώο")
            if st.button("⚡ Έναρξη Γρήγορου Τεστ", type="primary", key="go_quick_test", disabled=_quick_locked):
                pool = [q for q in all_questions if q["question"] in wrong_history]
                random.shuffle(pool)
                if not profile["quick_test_used"]:
                    mark_quick_test_used(user_id, token)
                st.session_state.update({
                    "quick_test_quiz": pool[:10], "quick_test_graded": False,
                })
                for k in list(st.session_state.keys()):
                    if k.startswith("qt_r_"):
                        del st.session_state[k]
                st.session_state["app_mode"] = "⚡ Γρήγορο Τεστ Μητρώου"
                st.rerun()

        st.write("")
        st.divider()

        # Reset επιλογές: μητρώο λαθών / ιστορικό επιδόσεων / και τα δύο
        with st.expander("⚙️ Ρυθμίσεις / Επαναφορά"):

            def _delete_all(table):
                headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {token}"}
                res = httpx.delete(
                    f"{URL}/rest/v1/{table}", headers=headers,
                    params={"user_id": f"eq.{user_id}"},
                )
                return res

            # ΣΗΜΑΝΤΙΚΟ: δεν μπορούμε να κάνουμε
            # st.session_state["confirm_reset_errors"] = False αφού το checkbox
            # με αυτό το key έχει ήδη σχεδιαστεί στο ίδιο run - το Streamlit το
            # απαγορεύει ρητά (StreamlitAPIException). Αντί να προσπαθούμε να
            # "ξετικάρουμε" το ίδιο widget, χρησιμοποιούμε ένα nonce στο key: σε
            # κάθε επιτυχή reset αυξάνουμε το nonce, οπότε στο επόμενο render το
            # checkbox παίρνει ΝΕΟ, καθαρό key (ξεκινάει αμέσως ξετικαρισμένο).
            for _nonce_key in ("reset_errors_nonce", "reset_history_nonce", "reset_both_nonce"):
                if _nonce_key not in st.session_state:
                    st.session_state[_nonce_key] = 0

            st.markdown("#### 🗑️ Καθαρισμός Μητρώου Λαθών")
            st.caption("Διαγράφει όλες τις ερωτήσεις από το μητρώο λαθών. Το ιστορικό επιδόσεων ΔΕΝ επηρεάζεται.")
            confirm_errors = st.checkbox(
                "Ναι, θέλω να καθαρίσω το μητρώο λαθών",
                key=f"confirm_reset_errors_{st.session_state['reset_errors_nonce']}",
            )
            if st.button("🗑️ Καθαρισμός Μητρώου Λαθών", disabled=not confirm_errors, key="btn_reset_errors"):
                try:
                    res = _delete_all("user_errors")
                    if res.status_code in (200, 204):
                        st.success("✅ Το μητρώο λαθών καθαρίστηκε.")
                        st.session_state["reset_errors_nonce"] += 1
                        time.sleep(1); st.rerun()
                    else:
                        st.error(f"Αποτυχία: HTTP {res.status_code} - {res.text[:200]}")
                except Exception as e:
                    st.error(f"Σφάλμα: {e}")

            st.divider()
            st.markdown("#### 🗑️ Καθαρισμός Ιστορικού Επιδόσεων")
            st.caption("Διαγράφει όλα τα τεστ από το ιστορικό επιδόσεων. Το μητρώο λαθών ΔΕΝ επηρεάζεται.")
            confirm_history = st.checkbox(
                "Ναι, θέλω να καθαρίσω το ιστορικό επιδόσεων",
                key=f"confirm_reset_history_{st.session_state['reset_history_nonce']}",
            )
            if st.button("🗑️ Καθαρισμός Ιστορικού", disabled=not confirm_history, key="btn_reset_history"):
                try:
                    res = _delete_all("user_history")
                    if res.status_code in (200, 204):
                        st.success("✅ Το ιστορικό επιδόσεων καθαρίστηκε.")
                        st.session_state["reset_history_nonce"] += 1
                        time.sleep(1); st.rerun()
                    else:
                        st.error(f"Αποτυχία: HTTP {res.status_code} - {res.text[:200]}")
                except Exception as e:
                    st.error(f"Σφάλμα: {e}")

            st.divider()
            st.markdown("#### 🗑️ Πλήρης Επαναφορά")
            st.caption(
                "Διαγράφει μητρώο λαθών, ιστορικό επιδόσεων, ΚΑΙ μηδενίζει τον "
                "κύκλο κάλυψης ύλης. Ξεκινάς εντελώς από το 0."
            )
            confirm_both = st.checkbox(
                "Ναι, θέλω πλήρη επαναφορά",
                key=f"confirm_reset_both_{st.session_state['reset_both_nonce']}",
            )
            if st.button("🗑️ Πλήρης Επαναφορά", disabled=not confirm_both, key="btn_reset_both", type="secondary"):
                try:
                    res1 = _delete_all("user_errors")
                    res2 = _delete_all("user_history")
                    res3 = _delete_all("quiz_progress")
                    res4 = _delete_all("question_cycle")
                    ok1, ok2 = res1.status_code in (200, 204), res2.status_code in (200, 204)
                    if ok1 and ok2:
                        st.success("✅ Πλήρης επαναφορά ολοκληρώθηκε. Ξεκινάς από το 0!")
                        st.session_state["reset_both_nonce"] += 1
                        st.session_state["_has_saved_progress"] = False
                        time.sleep(1); st.rerun()
                    else:
                        if not ok1: st.error(f"user_errors: HTTP {res1.status_code} - {res1.text[:200]}")
                        if not ok2: st.error(f"user_history: HTTP {res2.status_code} - {res2.text[:200]}")
                except Exception as e:
                    st.error(f"Σφάλμα: {e}")

    if app_mode == "📝 Τεστ Προσομοίωσης":
        if "quiz_started" not in st.session_state: st.session_state["quiz_started"] = False

        # Auto-start όταν ερχόμαστε από τον προθάλαμο: δεν χρειάζεται πια δεύτερη
        # οθόνη "Καλώς ήρθες + κουμπί Έναρξη", αφού ο προθάλαμος έχει ήδη κάρτα.
        if not st.session_state["quiz_started"]:
            if st.session_state.pop("_resume_requested", False):
                # --- Συνέχεια αποθηκευμένου τεστ (auto-save resume) ---
                _progress = load_quiz_progress(user_id, token)
                if _progress:
                    for k in list(st.session_state.keys()):
                        if k.startswith("r_"):
                            del st.session_state[k]  # καθαρισμός stale radio-widget state
                    st.session_state.update({
                        "current_quiz": _progress["quiz"],
                        "user_answers": _progress["answers"],
                        "current_page": _progress["current_page"],
                        "start_time": _progress["start_time"],
                        "submitted": False, "history_updated": False,
                        "quiz_started": True,
                    })
                    st.rerun()
                else:
                    st.warning("Δεν βρέθηκε αποθηκευμένη πρόοδος. Ξεκινάει νέο τεστ.")
                    st.session_state["_has_saved_progress"] = False

            # 2 ενεργές λάθος ερωτήσεις (wrong_history εδώ περιέχει ΜΟΝΟ mastered=false,
            # χάρη στο split που κάναμε στην κορυφή του script)
            wrong_pool = [q for q in all_questions if q["question"] in wrong_history]
            normal_pool = [q for q in all_questions if q["question"] not in wrong_history and q["question"] not in mastered_history]
            num_wrong = min(2, len(wrong_pool))

            # Κάλυψη χωρίς επανάληψη: οι "κανονικές" ερωτήσεις τραβιούνται
            # κατά προτεραιότητα από αυτές που ΔΕΝ έχεις ξαναδεί στον τρέχοντα
            # κύκλο, ώστε να μην ξαναβλέπεις την ίδια ερώτηση πριν εμφανιστούν
            # όλες τουλάχιστον μία φορά.
            _cycle = load_question_cycle(user_id, token)
            chosen_normal, new_seen, new_cycle_number = pick_normal_questions(
                normal_pool, min(25 - num_wrong, len(normal_pool)), _cycle["seen"], _cycle["cycle"]
            )
            final_quiz = random.sample(wrong_pool, num_wrong) + chosen_normal
            save_question_cycle(user_id, token, new_seen, new_cycle_number)

            # Recheck: αν υπάρχει "μαθημένη" ερώτηση που έχει περάσει το όριο
            # τεστ (MASTERY_RECHECK_AFTER_TESTS), τη βάζουμε μία φορά σε αυτό
            # το τεστ, αντικαθιστώντας μια τυχαία κανονική ερώτηση.
            current_test_num = len(test_history)  # τεστ που έχουν ήδη ολοκληρωθεί μέχρι τώρα
            due_for_recheck = [
                q for q in all_questions
                if q["question"] in mastered_history
                and mastered_history[q["question"]].get("mastered_at_test_num") is not None
                and (current_test_num - mastered_history[q["question"]]["mastered_at_test_num"]) >= MASTERY_RECHECK_AFTER_TESTS
            ]
            if due_for_recheck and len(final_quiz) > 0:
                recheck_q = random.choice(due_for_recheck)
                final_quiz[-1] = recheck_q  # αντικατάσταση της τελευταίας θέσης

            random.shuffle(final_quiz)
            for k in list(st.session_state.keys()):
                if k.startswith("r_"):
                    del st.session_state[k]  # καθαρισμός stale radio-widget state από προηγούμενο τεστ
            st.session_state.update({
                "current_quiz": final_quiz, "start_time": time.time(),
                "user_answers": {}, "submitted": False, "history_updated": False,
                "current_page": 0, "quiz_started": True,
            })
            save_quiz_progress(user_id, token, final_quiz, {}, 0, st.session_state["start_time"])
            st.session_state["_has_saved_progress"] = False
            st.rerun()
        else:
            elapsed = time.time() - st.session_state["start_time"]
            remaining = max(0, 1500 - int(elapsed))
            st.markdown(f"**⏳ Υπολειπόμενος Χρόνος: {remaining // 60:02d}:{remaining % 60:02d}**")
            
            if not st.session_state["submitted"]:
                p = st.session_state["current_page"]
                q = st.session_state["current_quiz"][p]
                total_q = len(st.session_state["current_quiz"])
                st.progress((p + 1) / total_q)
                st.caption(f"📌 Ερώτηση {p + 1} από {total_q} — Απομένουν {total_q - (p + 1)} ερωτήσεις")
                st.markdown(f"### {p+1}. {q['question']}")
                st.caption(f"📚 Θεματική Ενότητα: {q['source']}")
                
                options = q["options"]
                # Ασφαλής επιλογή index
                try:
                    current_idx = options.index(st.session_state["user_answers"][p]) if p in st.session_state["user_answers"] and st.session_state["user_answers"][p] in options else None
                except: current_idx = None
                    
                choice = st.radio("Επιλογές:", options, key=f"r_{p}", index=current_idx)
                st.session_state["user_answers"][p] = choice

                has_answer = st.session_state["user_answers"].get(p) is not None

                c1, c2 = st.columns(2)
                if p > 0 and c1.button("⬅️ Προηγούμενη"):
                    st.session_state["current_page"] -= 1
                    save_quiz_progress(
                        user_id, token, st.session_state["current_quiz"],
                        st.session_state["user_answers"], st.session_state["current_page"],
                        st.session_state["start_time"],
                    )
                    st.rerun()
                if p < total_q - 1:
                    if c2.button("Επόμενη ➡️", disabled=not has_answer):
                        st.session_state["current_page"] += 1
                        save_quiz_progress(
                            user_id, token, st.session_state["current_quiz"],
                            st.session_state["user_answers"], st.session_state["current_page"],
                            st.session_state["start_time"],
                        )
                        st.rerun()
                    if not has_answer:
                        st.caption("⚠️ Επίλεξε μια απάντηση για να συνεχίσεις.")
                else:
                    if c2.button("📊 Οριστική Υποβολή", disabled=not has_answer):
                        st.session_state["submitted"] = True; st.rerun()
                    if not has_answer:
                        st.caption("⚠️ Επίλεξε μια απάντηση για να υποβάλεις.")
            else:
                # --- Βήμα Α: υπολογισμός score + sync με Supabase, ΜΙΑ ΚΑΙ ΜΟΝΗ ΦΟΡΑ.
                # Πριν, αυτό το loop (score + sync_user_error) έτρεχε ξανά σε ΚΑΘΕ
                # rerun αυτής της σελίδας -> διπλά network calls κάθε φορά που το
                # session_state άλλαζε για οποιονδήποτε λόγο (π.χ. sidebar). Τώρα
                # τρέχει μόνο όταν history_updated == False, και το αποτέλεσμα
                # αποθηκεύεται στο session_state ώστε οι επόμενοι reruns να κάνουν
                # απλά display, χωρίς νέα network calls.
                if not st.session_state["history_updated"]:
                    score = 0
                    results_log = []
                    current_test_num = len(test_history) + 1  # το τεστ που μόλις ολοκληρώνεται
                    for idx, q in enumerate(st.session_state["current_quiz"]):
                        ans = st.session_state["user_answers"].get(idx)
                        is_correct = (ans == q["correct"])
                        if is_correct:
                            score += 1

                        # entry: πλήρες state της ερώτησης αν υπάρχει ήδη
                        # (είτε ενεργή στο wrong_history, είτε mastered) - αλλιώς None.
                        entry = wrong_history.get(q["question"]) or mastered_history.get(q["question"])
                        _, event = sync_user_error(
                            user_id, token, q["question"], is_correct, entry, current_test_num,
                        )

                        results_log.append({
                            "question": q["question"], "answer": ans,
                            "correct": q["correct"], "is_correct": is_correct,
                            "source": q.get("source", "Γενικές Γνώσεις"),
                            "event": event,
                        })

                    save_user_history(user_id, token, f"{score} / 25", f"{int((score/25)*100)}%")
                    if not is_premium:
                        increment_official_tests_used(user_id, token, profile["official_tests_used"] + 1)
                    delete_quiz_progress(user_id, token)  # το τεστ ολοκληρώθηκε - δεν χρειάζεται πια το auto-save
                    st.session_state["_has_saved_progress"] = False
                    st.session_state["quiz_results"] = {"score": score, "total": 25, "log": results_log}
                    st.session_state["history_updated"] = True
                    st.rerun()  # Εδώ γίνεται το refresh - τώρα το sidebar στο νέο
                                # render διαβάζει ΗΔΗ ενημερωμένα wrong_history/test_history
                                # από τη βάση, αφού οι παραπάνω κλήσεις έχουν ήδη ολοκληρωθεί.

                # --- Βήμα Β: εμφάνιση από cache - καμία επιπλέον κλήση δικτύου.
                st.subheader("🏁 Αποτελέσματα Διόρθωσης")
                results = st.session_state["quiz_results"]
                for idx, r in enumerate(results["log"]):
                    st.markdown(f"**{idx+1}. {r['question']}**")
                    st.caption(f"📚 Θεματική Ενότητα: {r['source']}")
                    st.write(f"Η απάντησή σου: {r['answer']}")
                    if r["is_correct"]:
                        st.success("🎯 Σωστό")
                    else:
                        st.error(f"❌ Λάθος (Σωστό: {r['correct']})")
                    if r.get("event") in EVENT_LABELS:
                        st.info(EVENT_LABELS[r["event"]])
                    st.markdown("---")

                st.metric("🏆 Τελικό Σκορ", f"{results['score']} / {results['total']}")
                if st.button("🔄 Επιστροφή στον Προθάλαμο"):
                    for k in ("quiz_started", "quiz_results"):
                        st.session_state.pop(k, None)
                    st.session_state["app_mode"] = "🏛️ Προθάλαμος"
                    st.rerun()

    elif app_mode == "📖 Μελέτη Μητρώου Λαθών":
        wrong_qs = [q for q in all_questions if q["question"] in wrong_history]

        st.subheader(f"📖 Μητρώο Λαθών ({len(wrong_qs)} ερωτήσεις)")

        if not wrong_qs:
            st.info("🎉 Δεν έχεις καμία καταγεγραμμένη λάθος απάντηση αυτή τη στιγμή.")
        else:
            # Ομαδοποίηση ερωτήσεων ανά θεματική ενότητα (source)
            grouped = {}
            for q in wrong_qs:
                section = q.get("source") or "Γενικές Γνώσεις"
                grouped.setdefault(section, []).append(q)

            # Ταξινόμηση ενοτήτων αλφαβητικά, και μέσα σε κάθε ενότητα
            # οι πιο συχνά λάθος ερωτήσεις πρώτες.
            for section in sorted(grouped.keys()):
                questions_in_section = grouped[section]
                questions_in_section.sort(
                    key=lambda q: wrong_history.get(q["question"], {}).get("wrong_count", 0),
                    reverse=True,
                )
                total_wrongs = sum(
                    wrong_history.get(q["question"], {}).get("wrong_count", 1)
                    for q in questions_in_section
                )

                with st.expander(
                    f"📚 {section} — {len(questions_in_section)} ερωτήσεις "
                    f"({total_wrongs} συνολικά λάθη)",
                    expanded=False,
                    key=f"exp::{section}",
                ):
                    # Reset των απαντήσεων μελέτης όταν ανοιγοκλείνει η ενότητα
                    exp_prev_states = st.session_state.setdefault("_exp_prev_state", {})
                    current_exp_state = st.session_state.get(f"exp::{section}", False)
                    if exp_prev_states.get(section) != current_exp_state:
                        prefix1 = f"study_answer::{section}::"
                        prefix2 = f"study_checked::{section}::"
                        prefix3 = f"radio::{section}::"
                        for k in list(st.session_state.keys()):
                            if k.startswith(prefix1) or k.startswith(prefix2) or k.startswith(prefix3):
                                del st.session_state[k]
                        exp_prev_states[section] = current_exp_state

                    for i, q in enumerate(questions_in_section, 1):
                        entry = wrong_history.get(q["question"], {})
                        count = entry.get("wrong_count", 1)
                        streak = entry.get("correct_streak", 0)
                        if count >= 3:
                            badge = "🔴"
                        elif count == 2:
                            badge = "🟠"
                        else:
                            badge = "🟡"

                        # ΣΗΜΑΝΤΙΚΟ: το key ΔΕΝ βασίζεται στο κείμενο της
                        # ερώτησης, γιατί αν υπάρχουν δύο ερωτήσεις με
                        # ακριβώς το ίδιο κείμενο στη βάση, θα συγκρουστούν
                        # (StreamlitDuplicateElementKey). Χρησιμοποιούμε
                        # section + θέση στη λίστα, που είναι πάντα μοναδικό.
                        widget_key = f"{section}::{i}"
                        answer_key = f"study_answer::{widget_key}"
                        checked_key = f"study_checked::{widget_key}"
                        if answer_key not in st.session_state:
                            st.session_state[answer_key] = None
                        if checked_key not in st.session_state:
                            st.session_state[checked_key] = False

                        with st.container(border=True):
                            st.markdown(f"**{i}. {q['question']}**")
                            caption_bits = [f"{badge} Λάθος **{count}** φορ{'ές' if count != 1 else 'ά'}"]
                            if streak > 0:
                                caption_bits.append(
                                    f"🔥 {streak}/{MASTERY_STREAK_REQUIRED} συνεχόμενες σωστές"
                                )
                            st.caption(" · ".join(caption_bits))

                            options = q.get("options", [])

                            if not st.session_state[checked_key]:
                                # --- Φάση 1: επιλογή απάντησης, καμία ένδειξη ακόμα ---
                                chosen = st.radio(
                                    "Επίλεξε απάντηση:",
                                    options,
                                    index=None,
                                    key=f"radio::{widget_key}",
                                )
                                if st.button("✅ Έλεγχος Απάντησης", key=f"check::{widget_key}"):
                                    if chosen is None:
                                        st.warning("Επίλεξε πρώτα μια απάντηση.")
                                    else:
                                        st.session_state[answer_key] = chosen
                                        st.session_state[checked_key] = True
                                        st.rerun()
                            else:
                                # --- Φάση 2: εμφάνιση αποτελέσματος ---
                                chosen = st.session_state[answer_key]
                                for opt in options:
                                    if opt == q["correct"]:
                                        st.markdown(f"- ✅ **{opt}**")
                                    elif opt == chosen:
                                        st.markdown(f"- ❌ ~~{opt}~~ (η επιλογή σου)")
                                    else:
                                        st.markdown(f"- {opt}")

                                if chosen == q["correct"]:
                                    st.success("🎯 Σωστά!")
                                else:
                                    st.error("❌ Λάθος αυτή τη φορά.")

                                if st.button("🔄 Ξανά", key=f"retry::{widget_key}"):
                                    st.session_state[checked_key] = False
                                    st.session_state[answer_key] = None
                                    st.session_state.pop(f"radio::{widget_key}", None)
                                    st.rerun()

    elif app_mode == "📊 Ιστορικό Επιδόσεων":
        st.title("📊 Ιστορικό Επιδόσεων")

        if not test_history:
            st.info("Δεν έχεις ολοκληρώσει κανένα τεστ ακόμα. Ξεκίνα το πρώτο σου από τον Προθάλαμο!")
        else:
            # Επεξεργασία δεδομένων: το score είναι text τύπου "8 / 25",
            # το percentage είναι text τύπου "32%". Τα μετατρέπουμε σε ints
            # για να μπορούμε να κάνουμε στατιστικά και γράφημα.
            parsed = []
            for entry in test_history:
                try:
                    score_num = int(entry["score"].split("/")[0].strip())
                except Exception:
                    score_num = 0
                try:
                    pct_num = int(str(entry["percentage"]).replace("%", "").strip())
                except Exception:
                    pct_num = 0
                parsed.append({
                    "score": score_num,
                    "percentage": pct_num,
                    "date": entry["created_at"][:10],
                    "datetime": entry["created_at"][:16].replace("T", " "),
                })

            # --- ΣΤΑΤΙΣΤΙΚΑ (4 μετρικές πάνω-πάνω) ---
            total_tests = len(parsed)
            avg_pct = round(sum(p["percentage"] for p in parsed) / total_tests, 1)
            best = max(parsed, key=lambda p: p["percentage"])
            worst = min(parsed, key=lambda p: p["percentage"])

            # Τάση: τελευταία 3 vs προηγούμενα 3 (αν υπάρχουν αρκετά τεστ)
            # Σημείωση: parsed είναι sorted desc (πιο πρόσφατο πρώτο), λόγω
            # του order=created_at.desc στο query.
            trend_label = ""
            if total_tests >= 6:
                recent_avg = sum(p["percentage"] for p in parsed[:3]) / 3
                older_avg = sum(p["percentage"] for p in parsed[3:6]) / 3
                diff = recent_avg - older_avg
                if diff > 2:
                    trend_label = f"↗️ +{diff:.1f}%"
                elif diff < -2:
                    trend_label = f"↘️ {diff:.1f}%"
                else:
                    trend_label = "➡️ Σταθερά"

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("📝 Συνολικά Τεστ", total_tests)
            c2.metric("📈 Μέσος Όρος", f"{avg_pct}%", delta=trend_label if trend_label else None)
            c3.metric("🏆 Καλύτερο Σκορ", f"{best['percentage']}%", help=f"{best['score']}/25 στις {best['date']}")
            c4.metric("📉 Χειρότερο Σκορ", f"{worst['percentage']}%", help=f"{worst['score']}/25 στις {worst['date']}")

            st.divider()

            # --- ΓΡΑΦΗΜΑ ΠΡΟΟΔΟΥ ---
            st.subheader("📈 Πρόοδος στον Χρόνο")
            # Αντιστροφή σε χρονολογική σειρά (παλαιό -> νέο) για το γράφημα
            chart_data = {
                "Τεστ #": list(range(1, total_tests + 1)),
                "Ποσοστό %": [p["percentage"] for p in reversed(parsed)],
            }
            st.line_chart(chart_data, x="Τεστ #", y="Ποσοστό %", height=300)

            # Πληροφορία: πόσα τεστ πάνω από τη βάση (>=50%)
            passed = sum(1 for p in parsed if p["percentage"] >= 50)
            st.caption(
                f"✅ Πάνω από τη βάση (≥50%): **{passed}** από {total_tests} τεστ "
                f"({round(100*passed/total_tests)}%)"
            )

            st.divider()

            # --- ΑΝΑΛΥΤΙΚΟΣ ΠΙΝΑΚΑΣ ---
            st.subheader("📋 Αναλυτικός Πίνακας")

            # Φίλτρο ελάχιστου ποσοστού
            min_pct = st.slider(
                "Εμφάνιση τεστ με ποσοστό ≥",
                min_value=0, max_value=100, value=0, step=10, format="%d%%",
            )
            filtered = [p for p in parsed if p["percentage"] >= min_pct]

            if not filtered:
                st.warning("Κανένα τεστ δεν πληροί το φίλτρο.")
            else:
                # Χτίζουμε rows με emoji ένδειξη επίδοσης
                table_rows = []
                for i, p in enumerate(filtered, 1):
                    if p["percentage"] >= 80:
                        badge = "🟢"
                    elif p["percentage"] >= 50:
                        badge = "🟡"
                    else:
                        badge = "🔴"
                    table_rows.append({
                        "#": len(filtered) - i + 1,  # πιο πρόσφατο -> μεγαλύτερος αριθμός
                        "Ημερομηνία": p["datetime"],
                        "Σκορ": f"{p['score']} / 25",
                        "Ποσοστό": f"{badge} {p['percentage']}%",
                    })
                st.dataframe(table_rows, use_container_width=True, hide_index=True)

    elif app_mode == "🎯 Εξάσκηση Ενότητας":
        quiz = st.session_state.get("practice_quiz", [])
        section = st.session_state.get("practice_section", "")
        st.title(f"🎯 Εξάσκηση: {section}")
        st.caption("Ελεύθερη εξάσκηση — δεν επηρεάζει το μητρώο λαθών ούτε το ιστορικό επιδόσεων.")

        if not quiz:
            st.info("Δεν υπάρχουν ερωτήσεις για αυτή την ενότητα.")
        elif not st.session_state.get("practice_submitted"):
            with st.form("practice_form"):
                for i, q in enumerate(quiz):
                    st.markdown(f"**{i + 1}. {q['question']}**")
                    st.radio(
                        "Επιλογές:", q.get("options", []), index=None,
                        key=f"practice_r_{i}",
                    )
                    st.write("")
                submitted = st.form_submit_button("✅ Έλεγχος Απαντήσεων", type="primary")
                if submitted:
                    st.session_state["practice_answers"] = {
                        i: st.session_state.get(f"practice_r_{i}") for i in range(len(quiz))
                    }
                    st.session_state["practice_submitted"] = True
                    st.rerun()
        else:
            answers = st.session_state.get("practice_answers", {})
            score = sum(1 for i, q in enumerate(quiz) if answers.get(i) == q["correct"])
            st.metric("Σκορ Εξάσκησης", f"{score} / {len(quiz)}")
            st.divider()
            for i, q in enumerate(quiz):
                ans = answers.get(i)
                with st.container(border=True):
                    st.markdown(f"**{i + 1}. {q['question']}**")
                    for opt in q.get("options", []):
                        if opt == q["correct"]:
                            st.markdown(f"- ✅ **{opt}**")
                        elif opt == ans:
                            st.markdown(f"- ❌ ~~{opt}~~ (η επιλογή σου)")
                        else:
                            st.markdown(f"- {opt}")

            st.write("")
            bcol1, bcol2 = st.columns(2)
            if bcol1.button("🔄 Νέα Εξάσκηση (ίδια ενότητα)", use_container_width=True):
                pool = [q for q in all_questions if (q.get("source") or "Γενικές Γνώσεις") == section]
                random.shuffle(pool)
                for k in list(st.session_state.keys()):
                    if k.startswith("practice_r_"):
                        del st.session_state[k]
                st.session_state.update({
                    "practice_quiz": pool[:20], "practice_answers": {},
                    "practice_submitted": False,
                })
                st.rerun()
            if bcol2.button("🏛️ Επιστροφή στον Προθάλαμο", use_container_width=True):
                for k in ("practice_quiz", "practice_answers", "practice_submitted", "practice_section"):
                    st.session_state.pop(k, None)
                for k in list(st.session_state.keys()):
                    if k.startswith("practice_r_"):
                        del st.session_state[k]
                st.session_state["app_mode"] = "🏛️ Προθάλαμος"
                st.rerun()

    elif app_mode == "⚡ Γρήγορο Τεστ Μητρώου":
        quiz = st.session_state.get("quick_test_quiz", [])
        st.title("⚡ Γρήγορο Τεστ Μητρώου Λαθών")
        st.caption(
            "Οι σωστές απαντήσεις εδώ μετράνε κανονικά στο σύστημα μάθησης "
            "(3 συνεχόμενες σωστές = μαθημένη). ΔΕΝ προστίθεται στο Ιστορικό Επιδόσεων."
        )

        if not quiz:
            st.info("Δεν υπάρχουν ερωτήσεις στο μητρώο λαθών αυτή τη στιγμή. 🎉")
            if st.button("🏛️ Επιστροφή στον Προθάλαμο"):
                st.session_state["app_mode"] = "🏛️ Προθάλαμος"; st.rerun()

        elif not st.session_state.get("quick_test_graded"):
            with st.form("quick_test_form"):
                for i, q in enumerate(quiz):
                    st.markdown(f"**{i + 1}. {q['question']}**")
                    st.caption(f"📚 {q.get('source', 'Γενικές Γνώσεις')}")
                    st.radio("Επιλογές:", q.get("options", []), index=None, key=f"qt_r_{i}")
                    st.write("")
                submitted = st.form_submit_button("✅ Υποβολή Γρήγορου Τεστ", type="primary")
                if submitted:
                    results_log = []
                    current_test_num = len(test_history)
                    for i, q in enumerate(quiz):
                        ans = st.session_state.get(f"qt_r_{i}")
                        is_correct = (ans == q["correct"])
                        entry = wrong_history.get(q["question"]) or mastered_history.get(q["question"])
                        _, event = sync_user_error(user_id, token, q["question"], is_correct, entry, current_test_num)
                        results_log.append({
                            "question": q["question"], "answer": ans, "correct": q["correct"],
                            "is_correct": is_correct, "source": q.get("source", "Γενικές Γνώσεις"),
                            "event": event,
                        })
                    st.session_state["quick_test_results"] = results_log
                    st.session_state["quick_test_graded"] = True
                    st.rerun()

        else:
            results = st.session_state.get("quick_test_results", [])
            score = sum(1 for r in results if r["is_correct"])
            st.metric("Σκορ", f"{score} / {len(results)}")
            st.divider()
            for idx, r in enumerate(results):
                st.markdown(f"**{idx + 1}. {r['question']}**")
                st.caption(f"📚 {r['source']}")
                st.write(f"Η απάντησή σου: {r['answer']}")
                if r["is_correct"]:
                    st.success("🎯 Σωστό")
                else:
                    st.error(f"❌ Λάθος (Σωστό: {r['correct']})")
                if r.get("event") in EVENT_LABELS:
                    st.info(EVENT_LABELS[r["event"]])
                st.markdown("---")

            bcol1, bcol2 = st.columns(2)
            if bcol1.button("🔄 Νέο Γρήγορο Τεστ", use_container_width=True):
                pool = [q for q in all_questions if q["question"] in wrong_history]
                random.shuffle(pool)
                for k in list(st.session_state.keys()):
                    if k.startswith("qt_r_"):
                        del st.session_state[k]
                st.session_state.update({
                    "quick_test_quiz": pool[:10], "quick_test_graded": False,
                })
                st.rerun()
            if bcol2.button("🏛️ Επιστροφή στον Προθάλαμο", use_container_width=True):
                for k in ("quick_test_quiz", "quick_test_graded", "quick_test_results"):
                    st.session_state.pop(k, None)
                for k in list(st.session_state.keys()):
                    if k.startswith("qt_r_"):
                        del st.session_state[k]
                st.session_state["app_mode"] = "🏛️ Προθάλαμος"
                st.rerun()

    elif app_mode == "ℹ️ Οδηγός Χρήσης":
        st.title("ℹ️ Πώς λειτουργεί η εφαρμογή")

        st.markdown("""
Καλώς ήρθες! Εδώ εξηγούμε αναλυτικά όλες τις λειτουργίες, ώστε να ξέρεις
ακριβώς τι κάνει το κάθε κουμπί και πώς λειτουργεί το σύστημα από πίσω.
""")

        st.subheader("📝 Τεστ Προσομοίωσης")
        st.markdown("""
- Κάθε επίσημο τεστ έχει **25 ερωτήσεις**: έως **2 ερωτήσεις** τραβιούνται
  από το ενεργό μητρώο λαθών σου, οι υπόλοιπες είναι νέες ερωτήσεις.
- Έχει χρονόμετρο **25 λεπτών**.
- Δεν μπορείς να προχωρήσεις σε επόμενη ερώτηση χωρίς να απαντήσεις.
- Μόλις υποβάλεις, το σκορ αποθηκεύεται στο **Ιστορικό Επιδόσεων**, και
  κάθε απάντηση ενημερώνει το **Μητρώο Λαθών** (δες παρακάτω πώς).
""")

        st.subheader("📚 Κάλυψη ύλης χωρίς επανάληψη")
        st.markdown("""
- Οι "κανονικές" ερωτήσεις κάθε τεστ τραβιούνται με προτεραιότητα από
  ερωτήσεις που **δεν** έχεις ξαναδεί στον τρέχοντα κύκλο — σαν τράπουλα
  που δεν ξαναβάζει μέσα τα χαρτιά που έχουν βγει.
- Έτσι εγγυόμαστε ότι θα δεις **όλη την ύλη** πριν ξαναδείς την ίδια
  ερώτηση από την αρχή.
- Όταν εξαντληθούν όλες οι μοναδικές ερωτήσεις, ανοίγει αυτόματα νέος
  **κύκλος** (το μετράει η μπάρα προόδου στον Προθάλαμο).
""")

        st.subheader("📖 Μητρώο Λαθών — πώς ενημερώνεται")
        st.markdown("""
- Κάθε φορά που απαντάς **λάθος** μια ερώτηση (σε επίσημο τεστ ή στο
  Γρήγορο Τεστ Μητρώου), η ερώτηση μπαίνει (ή παραμένει) στο μητρώο, και
  ο μετρητής **"φορές λάθος"** αυξάνεται κατά 1.
- Κάθε φορά που απαντάς **σωστά** μια ερώτηση που ήδη βρίσκεται στο
  μητρώο, μετράμε πόσες **συνεχόμενες** φορές την πέτυχες.
- Στις **3 συνεχόμενες σωστές**, η ερώτηση θεωρείται **"μαθημένη"** 🎓 και
  βγαίνει αυτόματα από το ενεργό μητρώο — δεν σε ξαναενοχλεί, ούτε
  εμφανίζεται πια στη λίστα.
- Αν όμως κάνεις λάθος έστω και μία φορά πριν φτάσεις τις 3 συνεχόμενες
  σωστές, ο μετρητής **μηδενίζεται** και ξεκινάει από την αρχή.
- Μια **"μαθημένη"** ερώτηση δεν διαγράφεται αμέσως οριστικά: μετά από
  **5 τεστ**, επανεμφανίζεται **μία φορά** ως επαλήθευση. Αν την πετύχεις
  ξανά, αφαιρείται **οριστικά** 🏆. Αν την ξανακάνεις λάθος, επιστρέφει
  κανονικά στο ενεργό μητρώο, σαν να μην την είχες μάθει ποτέ.
""")

        st.subheader("📖 Μελέτη Μητρώου Λαθών")
        st.markdown("""
- Δες όλες τις ενεργές ερωτήσεις του μητρώου, **ομαδοποιημένες ανά
  θεματική ενότητα**.
- Μπορείς να διαλέξεις απάντηση και να πατήσεις "Έλεγχος" για να δεις αν
  ήταν σωστή — **αυτό είναι ελεύθερη εξάσκηση και ΔΕΝ επηρεάζει** τον
  μετρητή μάθησης. Μόνο οι απαντήσεις σε πραγματικά τεστ (επίσημο ή
  Γρήγορο Τεστ Μητρώου) μετράνε για το mastery.
""")

        st.subheader("🎯 Γρήγορη Εξάσκηση ανά Ενότητα")
        st.markdown("""
- Διαλέγεις μια θεματική ενότητα και κάνεις πρακτική σε ερωτήσεις από
  αυτή, χωρίς χρονόμετρο.
- **Καμία επίδραση** στο μητρώο λαθών ή στο ιστορικό επιδόσεων — καθαρά
  για εξάσκηση.
""")

        st.subheader("⚡ Γρήγορο Τεστ Μητρώου Λαθών")
        st.markdown("""
- Τεστ με έως **10 ερωτήσεις μόνο από το ενεργό μητρώο λαθών** σου, χωρίς
  χρονόμετρο.
- Οι απαντήσεις εδώ **μετράνε κανονικά** στο σύστημα μάθησης (mastery),
  ακριβώς όπως σε επίσημο τεστ.
- **ΔΕΝ** προστίθεται στο Ιστορικό Επιδόσεων — είναι καθαρά focused
  εξάσκηση στα λάθη σου.
""")

        st.subheader("📊 Ιστορικό Επιδόσεων")
        st.markdown("""
- Δείχνει στατιστικά (μέσο όρο, καλύτερο/χειρότερο σκορ, τάση προόδου),
  γράφημα προόδου στον χρόνο, και αναλυτικό πίνακα όλων των **επίσημων**
  τεστ που έχεις ολοκληρώσει. Το Γρήγορο Τεστ Μητρώου δεν προσμετράται εδώ.
""")

        st.subheader("💾 Auto-save")
        st.markdown("""
- Κατά τη διάρκεια ενός επίσημου τεστ, η πρόοδός σου αποθηκεύεται
  αυτόματα μετά από κάθε "Επόμενη/Προηγούμενη".
- Αν κλείσεις τον browser ή χαθεί η σύνδεση, στην επόμενη είσοδο θα δεις
  ειδοποίηση στον Προθάλαμο για να συνεχίσεις από εκεί που έμεινες.
""")

        st.subheader("⭐ Δωρεάν vs Premium")
        st.markdown("""
- **Δωρεάν λογαριασμός:** 2 Επίσημα Τεστ συνολικά, 1 δωρεάν Εξάσκηση ανά
  θεματική ενότητα, 1 δωρεάν Γρήγορο Τεστ Μητρώου. Το Μητρώο Λαθών και το
  Ιστορικό Επιδόσεων είναι **πάντα** πλήρως προσβάσιμα, χωρίς όριο.
- **Premium (69€ εφάπαξ):** απεριόριστα σε όλα τα παραπάνω, για πάντα -
  καμία συνδρομή. Δεν είναι ακόμα διαθέσιμο για αγορά - μπορείς να δηλώσεις
  ενδιαφέρον από τον Προθάλαμο για να ενημερωθείς μόλις ανοίξει.
- Το "Πλήρης Επαναφορά" **δεν** επαναφέρει τα δωρεάν όρια χρήσης.
""")

        st.subheader("⚙️ Ρυθμίσεις / Επαναφορά")
        st.markdown("""
- Στον Προθάλαμο υπάρχουν 3 ξεχωριστές επιλογές καθαρισμού:
  **μόνο μητρώο λαθών**, **μόνο ιστορικό επιδόσεων**, ή **και τα δύο**.
- Κάθε επιλογή χρειάζεται ρητή επιβεβαίωση (checkbox) πριν ενεργοποιηθεί
  το κουμπί διαγραφής, ώστε να μη γίνει κάτι κατά λάθος.
""")

        st.write("")
        if st.button("🏛️ Επιστροφή στον Προθάλαμο", type="primary"):
            st.session_state["app_mode"] = "🏛️ Προθάλαμος"
            st.rerun()

    elif app_mode == "📜 Όροι & Απόρρητο":
        st.title("📜 Όροι Χρήσης & Πολιτική Απορρήτου")
        st.markdown(TERMS_AND_PRIVACY_TEXT)
        st.write("")
        if st.button("🏛️ Επιστροφή στον Προθάλαμο", type="primary", key="terms_back"):
            st.session_state["app_mode"] = "🏛️ Προθάλαμος"
            st.rerun()

    elif app_mode == "🛠️ Admin Panel":
        # Επανέλεγχος ασφαλείας εδώ επίσης (όχι μόνο στο sidebar) - ώστε ΚΑΝΕΝΑΣ
        # να μην μπορεί να δει τη σελίδα χειραγωγώντας απλά το app_mode.
        if not (ADMIN_EMAIL and st.session_state["auth"].get("email") == ADMIN_EMAIL):
            st.error("⛔ Δεν έχεις πρόσβαση σε αυτή τη σελίδα.")
            st.stop()

        st.title("🛠️ Διαχειριστικό Πάνελ")

        if not SERVICE_ROLE_KEY:
            st.error(
                "⚠️ Λείπει το SUPABASE_SERVICE_ROLE_KEY από τα secrets - το admin "
                "panel δεν μπορεί να λειτουργήσει χωρίς αυτό."
            )
            st.stop()

        tab_users, tab_requests, tab_premium, tab_notif = st.tabs([
            "👥 Όλοι οι Χρήστες", "🔔 Αιτήματα Premium", "⭐ Premium Χρήστες", "📨 Ειδοποιήσεις",
        ])

        # ------------------------------------------------------------------
        # TAB: Όλοι οι Χρήστες - λίστα, αναζήτηση, στατιστικά, ban/delete
        # ------------------------------------------------------------------
        with tab_users:
            all_users = admin_list_all_users_full()
            total = len(all_users)
            premium_count = sum(1 for u in all_users if u["is_premium"])
            free_count = total - premium_count
            banned_count = sum(1 for u in all_users if u["is_banned"])

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Σύνολο Χρηστών", total)
            c2.metric("Δωρεάν", free_count)
            c3.metric("⭐ Premium", premium_count)
            c4.metric("🚫 Banned", banned_count)

            st.divider()
            search = st.text_input("🔍 Αναζήτηση με email", key="admin_user_search")
            filtered = [u for u in all_users if search.lower() in u["email"].lower()] if search else all_users

            st.caption(f"Εμφανίζονται {len(filtered)} από {total} χρήστες, πιο πρόσφατοι πρώτα.")

            for u in filtered:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        badges = []
                        if u["is_premium"]:
                            badges.append("⭐ Premium")
                        else:
                            badges.append("🆓 Δωρεάν")
                        if u["is_banned"]:
                            badges.append("🚫 BANNED")
                        if u["premium_interest"] and not u["is_premium"]:
                            badges.append("🔔 Ζήτησε Premium")
                        st.markdown(f"**{u['email']}** — {' · '.join(badges)}")
                        st.caption(
                            f"Εγγραφή: {u['created_at'][:16].replace('T',' ')} · "
                            f"Τελευταία σύνδεση: {u['last_sign_in_at'][:16].replace('T',' ') if u['last_sign_in_at'] != '-' else '-'} · "
                            f"Τεστ: {u['official_tests_used']} · Γρήγορο Τεστ: {'Ναι' if u['quick_test_used'] else 'Όχι'} · "
                            f"Ενότητες εξάσκησης: {u['sections_practiced']} · "
                            f"Συσκευή: {guess_device_type(u['user_agent'])}"
                        )
                        with st.expander("🔎 Πλήρες User-Agent"):
                            st.code(u["user_agent"], language=None)
                    with col2:
                        ban_label = "✅ Unban" if u["is_banned"] else "🚫 Ban"
                        if st.button(ban_label, key=f"ban_{u['user_id']}", use_container_width=True):
                            if admin_ban_user(u["user_id"], not u["is_banned"]):
                                st.success("Έγινε.")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Απέτυχε.")

                        _confirm_key = f"confirm_delete_{u['user_id']}"
                        if st.session_state.get(_confirm_key):
                            st.warning("Οριστική διαγραφή - σίγουρα;")
                            dc1, dc2 = st.columns(2)
                            if dc1.button("Ναι", key=f"del_yes_{u['user_id']}", use_container_width=True):
                                if admin_delete_user_completely(u["user_id"]):
                                    st.success("Διαγράφηκε.")
                                    st.session_state[_confirm_key] = False
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("Απέτυχε.")
                            if dc2.button("Άκυρο", key=f"del_no_{u['user_id']}", use_container_width=True):
                                st.session_state[_confirm_key] = False
                                st.rerun()
                        else:
                            if st.button("🗑️ Διαγραφή", key=f"del_{u['user_id']}", use_container_width=True):
                                st.session_state[_confirm_key] = True
                                st.rerun()

        # ------------------------------------------------------------------
        # TAB: Αιτήματα Premium
        # ------------------------------------------------------------------
        with tab_requests:
            st.subheader("🔔 Αιτήματα Πρόσβασης Premium")
            pending = admin_list_pending_premium_requests()
            if not pending:
                st.info("Δεν υπάρχουν εκκρεμή αιτήματα αυτή τη στιγμή.")
            else:
                for req in pending:
                    with st.container(border=True):
                        col1, col2 = st.columns([3, 1])
                        col1.write(f"📧 {req['email']}")
                        if col2.button("✅ Έγκριση", key=f"approve_{req['user_id']}", use_container_width=True):
                            if admin_set_premium(req["user_id"], True):
                                st.success(f"Εγκρίθηκε: {req['email']}")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Κάτι πήγε στραβά.")

        # ------------------------------------------------------------------
        # TAB: Ενεργοί Premium Χρήστες
        # ------------------------------------------------------------------
        with tab_premium:
            st.subheader("⭐ Ενεργοί Premium Χρήστες")
            premium_users = admin_list_all_premium()
            if not premium_users:
                st.info("Κανένας premium χρήστης ακόμα.")
            else:
                for pu in premium_users:
                    with st.container(border=True):
                        col1, col2 = st.columns([3, 1])
                        col1.write(f"⭐ {pu['email']}")
                        if col2.button("🚫 Αφαίρεση", key=f"revoke_{pu['user_id']}", use_container_width=True):
                            if admin_set_premium(pu["user_id"], False):
                                st.success(f"Αφαιρέθηκε: {pu['email']}")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Κάτι πήγε στραβά.")

        # ------------------------------------------------------------------
        # TAB: Ειδοποιήσεις (email log)
        # ------------------------------------------------------------------
        with tab_notif:
            st.subheader("📨 Πρόσφατες Ειδοποιήσεις (email log)")
            st.caption(
                "Κάθε απόπειρα αποστολής email (νέα εγγραφή, αίτημα Premium) καταγράφεται εδώ "
                "μόνιμα - ανεξάρτητα από session/συσκευή, άρα πάντα αξιόπιστο σημείο ελέγχου."
            )
            notif_log = admin_list_recent_notifications()
            if not notif_log:
                st.info("Καμία καταγεγραμμένη ειδοποίηση ακόμα.")
            else:
                for entry in notif_log:
                    icon = "✅" if entry.get("success") else "❌"
                    st.write(
                        f"{icon} **{entry.get('event_type')}** — {entry.get('created_at', '')[:16].replace('T', ' ')} "
                        f"— {entry.get('detail', '')}"
                    )

        st.write("")
        if st.button("🏛️ Επιστροφή στον Προθάλαμο", type="primary", key="admin_back"):
            st.session_state["app_mode"] = "🏛️ Προθάλαμος"
            st.rerun()