import streamlit as st
import pandas as pd
import datetime
import requests
import base64
import io
import time
import json
import re
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

st.set_page_config(page_title="ממיר דוח נוכחות", page_icon="📅", layout="centered")

st.title("📅 ממיר דוח נוכחות לאקסל")
st.write("העלה תמונה של הדו\"ח וקבל קובץ אקסל מעובד ומאוזן לפי 9 שעות יומית.")

# ---------------------------------------------------------------------------
# Interactions API — הנתיב היחיד שתומך במפתחות הרשאה בפורמט AQ.
# ---------------------------------------------------------------------------
API_ROOT = "https://generativelanguage.googleapis.com"
INTERACTIONS_URL = f"{API_ROOT}/v1beta/interactions"

MODELS_TO_TRY = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "day": {"type": "string"},
                    "worked": {"type": "boolean"},
                    "gross": {"type": "string"},
                },
                "required": ["date", "day", "worked", "gross"],
            },
        }
    },
    "required": ["days"],
}

PROMPT = (
    "חלץ מתמונת דוח הנוכחות את כל הימים בחודש, לפי הסדר.\n"
    "עבור כל יום החזר:\n"
    "date - התאריך כפי שמופיע בדוח, בפורמט DD/MM/YY.\n"
    "day - אות היום בעברית כפי שמופיעה בדוח (א, ב, ג, ד, ה, ו, שב).\n"
    "worked - true אם יש דיווח נוכחות באותו יום, אחרת false.\n"
    "gross - סך השעות ברוטו בפורמט HH:MM. אם אין דיווח, החזר מחרוזת ריקה.\n"
    "אל תדלג על ימים ואל תמציא נתונים."
)

# ---------------------------------------------------------------------------
# ניהול מפתח — נטען אוטומטית מ-Secrets של Streamlit Cloud
# ---------------------------------------------------------------------------
secret_key = ""
try:
    secret_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    secret_key = ""

if secret_key:
    user_api_key = secret_key
    st.caption("🔑 המפתח נטען מהגדרות ה-Secrets של האפליקציה.")
else:
    user_api_key = st.text_input(
        "מפתח Gemini API:",
        value="",
        type="password",
        help="הגדר GEMINI_API_KEY תחת Manage app → Settings → Secrets כדי לא להזין כל פעם.",
    )


def auth_headers(key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": str(key).strip(),
    }


# ---------------------------------------------------------------------------
# תקשורת מול ה-API — לעולם לא זורקת חריגה
# ---------------------------------------------------------------------------
def call_interactions(key: str, body: dict, timeout: int = 180, retries: int = 1):
    last_err = {"code": None, "message": "שגיאה לא ידועה"}

    for attempt in range(retries + 1):
        try:
            res = requests.post(
                INTERACTIONS_URL,
                headers=auth_headers(key),
                json=body,
                timeout=(15, timeout),  # (חיבור, קריאה)
            )
        except requests.exceptions.ReadTimeout:
            last_err = {"code": "timeout", "message": f"המודל לא הגיב תוך {timeout} שניות."}
            continue
        except requests.exceptions.ConnectTimeout:
            last_err = {"code": "timeout", "message": "פסק זמן בהתחברות לשרת של גוגל."}
            continue
        except requests.exceptions.ConnectionError as e:
            last_err = {"code": "network", "message": f"בעיית רשת: {e}"}
            continue
        except requests.exceptions.RequestException as e:
            last_err = {"code": "request", "message": str(e)}
            break

        try:
            data = res.json()
        except ValueError:
            snippet = (res.text or "")[:300]
            return False, {
                "code": res.status_code,
                "message": f"תגובה שאינה JSON (HTTP {res.status_code}): {snippet}",
            }

        if res.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            code = err.get("code", res.status_code)
            msg = err.get("message", str(data))
            if code in (429, 500, 503) and attempt < retries:
                time.sleep(2)
                last_err = {"code": code, "message": msg}
                continue
            return False, {"code": code, "message": msg}

        return True, data

    return False, last_err


def ping_server(timeout: int = 10):
    """בדיקה מהירה שהשרת של גוגל נגיש בכלל, ללא תלות במפתח."""
    try:
        requests.head(API_ROOT, timeout=timeout)
        return True, ""
    except requests.exceptions.RequestException as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# הרצה עם אינדיקציה חיה
# ---------------------------------------------------------------------------
def stage_text(elapsed: float) -> str:
    if elapsed < 3:
        return "פותח חיבור לשרת של גוגל"
    if elapsed < 10:
        return "שולח את הבקשה"
    if elapsed < 30:
        return "המודל מעבד את הבקשה"
    if elapsed < 90:
        return "המודל עדיין עובד, זה תקין עבור תמונות מפורטות"
    return "לוקח יותר מהרגיל, ממתין לתשובה"


def run_with_progress(key: str, body: dict, timeout: int, retries: int, headline: str):
    """מריץ את הקריאה ב-thread ומעדכן את המסך בזמן אמת."""
    status_box = st.empty()
    bar = st.progress(0)
    started = time.time()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call_interactions, key, body, timeout, retries)

        while not future.done():
            elapsed = time.time() - started
            status_box.info(
                f"⏳ {headline} — {stage_text(elapsed)}… ({elapsed:.0f} שניות)"
            )
            bar.progress(min(int((elapsed / timeout) * 100), 99))
            time.sleep(0.2)

        ok, result = future.result()

    elapsed = time.time() - started
    bar.progress(100)
    time.sleep(0.15)
    bar.empty()
    status_box.empty()
    return ok, result, elapsed


# ---------------------------------------------------------------------------
# חילוץ הטקסט מתשובת ה-API
# ---------------------------------------------------------------------------
def extract_output_text(payload) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    chunks = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                chunks.append(node["text"])
            elif isinstance(node.get("output_text"), str):
                chunks.append(node["output_text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return "\n".join(chunks).strip()


def parse_days(text: str):
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    if isinstance(data, dict):
        return data.get("days", [])
    return data


# ---------------------------------------------------------------------------
# עיבוד הנתונים
# ---------------------------------------------------------------------------
WEEKEND_NAMES = {"ו", "ש", "שב", "שישי", "שבת"}


def process_attendance_data(raw_days):
    processed_rows = []

    for item in raw_days:
        date_str = str(item.get("date", "")).strip()
        is_worked = bool(item.get("worked", False))
        day_name = str(item.get("day", "")).strip()
        gross_hhmm = str(item.get("gross", "") or "").strip()

        is_weekend = day_name in WEEKEND_NAMES

        if is_worked and gross_hhmm:
            try:
                h_str, m_str = gross_hhmm.split(":")[:2]
                total_min = int(h_str) * 60 + int(m_str)
                decimal_qty = round((round(total_min / 15.0) * 15) / 60.0, 2)
            except Exception:
                decimal_qty = 0.0

            processed_rows.append({
                'מק"ט': "100101",
                "תאור מוצר": date_str,
                "כמות": decimal_qty,
                "עבודה מהבית?": "",
            })

            if not is_weekend and decimal_qty < 9.00:
                processed_rows.append({
                    'מק"ט': "100101",
                    "תאור מוצר": date_str,
                    "כמות": round(9.00 - decimal_qty, 2),
                    "עבודה מהבית?": "Y",
                })

        elif not is_weekend:
            processed_rows.append({
                'מק"ט': "100101",
                "תאור מוצר": date_str,
                "כמות": 9.00,
                "עבודה מהבית?": "Y",
            })

    return pd.DataFrame(processed_rows)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="נוכחות")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# בדיקת חיבור
# ---------------------------------------------------------------------------
with st.expander("🔧 בדיקת חיבור"):
    st.caption("שולח בקשת טקסט קצרה כדי לוודא שהמפתח והמודל עובדים.")
    if st.button("שלח בקשת בדיקה"):
        if not str(user_api_key).strip():
            st.error("אנא הזן מפתח API.")
        else:
            net_box = st.empty()
            net_box.info("🌐 בודק נגישות לשרת של גוגל…")
            reachable, net_err = ping_server()
            if not reachable:
                net_box.empty()
                st.error(f"אין גישה לשרת של גוגל: {net_err}")
            else:
                net_box.success("🌐 השרת נגיש. שולח בקשה למודל…")
                ok, result, elapsed = run_with_progress(
                    user_api_key,
                    {
                        "model": MODELS_TO_TRY[0],
                        "input": "החזר את המילה תקין בלבד",
                        "generation_config": {"thinking_level": "minimal"},
                    },
                    timeout=60,
                    retries=1,
                    headline="בודק תקשורת",
                )
                net_box.empty()

                if ok:
                    st.success(
                        f"✅ החיבור תקין ({elapsed:.1f} שניות). "
                        f"תגובת המודל: {extract_output_text(result)}"
                    )
                else:
                    st.error(
                        f"❌ שגיאה [{result.get('code')}] אחרי {elapsed:.1f} שניות: "
                        f"{result.get('message')}"
                    )


# ---------------------------------------------------------------------------
# העלאה ועיבוד
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader("צלם או העלה תמונה של הדו\"ח:", type=["jpg", "jpeg", "png"])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="התמונה שהועלתה", use_container_width=True)

    if st.button("🚀 עבד והפק אקסל", use_container_width=True):
        if not str(user_api_key).strip():
            st.error("אנא ודא שמפתח ה-API מוגדר.")
        else:
            try:
                prep_box = st.empty()
                prep_box.info("🖼️ מכין את התמונה לשליחה…")

                img = image.convert("RGB")
                max_side = 2000
                if max(img.size) > max_side:
                    ratio = max_side / max(img.size)
                    img = img.resize(
                        (int(img.width * ratio), int(img.height * ratio)),
                        Image.LANCZOS,
                    )

                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=90)
                img_bytes = buffered.getvalue()
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")

                prep_box.info(
                    f"🖼️ התמונה מוכנה ({len(img_bytes) / 1024:.0f} KB, "
                    f"{img.width}x{img.height})."
                )

                result_data = None
                last_error = {}

                for model_name in MODELS_TO_TRY:
                    body = {
                        "model": model_name,
                        "input": [
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image",
                                "data": img_b64,
                                "mime_type": "image/jpeg",
                            },
                        ],
                        "response_format": {
                            "type": "text",
                            "mime_type": "application/json",
                            "schema": RESPONSE_SCHEMA,
                        },
                        "generation_config": {"thinking_level": "minimal"},
                    }

                    ok, result, elapsed = run_with_progress(
                        user_api_key,
                        body,
                        timeout=180,
                        retries=1,
                        headline=f"מפענח באמצעות {model_name}",
                    )

                    if ok:
                        result_data = result
                        prep_box.success(f"📡 התקבלה תשובה תוך {elapsed:.1f} שניות.")
                        break

                    last_error = result
                    if result.get("code") in (401, 403):
                        break  # בעיית הרשאה — אין טעם לנסות מודל אחר
                    st.warning(
                        f"{model_name} נכשל [{result.get('code')}], מנסה מודל הבא…"
                    )

                if not result_data:
                    prep_box.empty()
                    st.error(
                        f"שגיאת תקשורת מול גוגל [{last_error.get('code')}]: "
                        f"{last_error.get('message')}"
                    )
                    if last_error.get("code") == "timeout":
                        st.info("נסה שוב, או צלם את הדוח ברזולוציה נמוכה יותר.")
                else:
                    raw_text = extract_output_text(result_data)
                    if not raw_text:
                        st.error("המודל לא החזיר טקסט. תגובה גולמית:")
                        st.json(result_data)
                    else:
                        raw_days = parse_days(raw_text)
                        df = process_attendance_data(raw_days)

                        if df.empty:
                            st.warning("לא זוהו ימים בדוח. נסה תמונה חדה יותר.")
                            st.code(raw_text)
                        else:
                            st.success("העיבוד הושלם בהצלחה!")
                            st.dataframe(df, use_container_width=True)
                            st.caption(f"סה\"כ שעות בדוח: {df['כמות'].sum():.2f}")

                            st.download_button(
                                label="📥 הורד קובץ אקסל",
                                data=to_excel_bytes(df),
                                file_name=f"attendance_{datetime.date.today()}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )

            except Exception as e:
                st.error(f"שגיאה בעיבוד: {e}")
