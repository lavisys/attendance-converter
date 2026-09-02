import streamlit as st
import pandas as pd
import datetime
import requests
import base64
import io
import time
import json
import re
from PIL import Image

st.set_page_config(page_title="ממיר דוח נוכחות", page_icon="📅", layout="centered")

st.title("📅 ממיר דוח נוכחות לאקסל")
st.write("העלה תמונה של הדו\"ח וקבל קובץ אקסל מעובד ומאוזן לפי 9 שעות יומית.")

# ---------------------------------------------------------------------------
# Interactions API — הנתיב היחיד שתומך במפתחות הרשאה בפורמט AQ.
# ---------------------------------------------------------------------------
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

MODELS_TO_TRY = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

# סכימת JSON שמכריחה את המודל להחזיר מבנה קבוע
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
# ניהול מפתח
# ---------------------------------------------------------------------------
default_key = ""
try:
    default_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    default_key = ""

user_api_key = st.text_input(
    "מפתח Gemini API:",
    value=default_key,
    type="password",
    help="מפתחות חדשים מ-AI Studio מתחילים ב-AQ. וזה תקין. https://aistudio.google.com/apikey",
)


def auth_headers(key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": key.strip(),
    }


# ---------------------------------------------------------------------------
# חילוץ הטקסט מתשובת ה-Interactions API
# ---------------------------------------------------------------------------
def extract_output_text(payload) -> str:
    """אוסף את כל בלוקי הטקסט מתשובת ה-API, בלי להסתמך על מבנה קשיח."""
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
                # עיגול לרבע השעה הקרובה
                decimal_qty = round((round(total_min / 15.0) * 15) / 60.0, 2)
            except Exception:
                decimal_qty = 0.0

            # שורת עבודה במשרד
            processed_rows.append({
                'מק"ט': "100101",
                "תאור מוצר": date_str,
                "כמות": decimal_qty,
                "עבודה מהבית?": "",
            })

            # השלמה ל-9 שעות יומית בימי חול
            if not is_weekend and decimal_qty < 9.00:
                processed_rows.append({
                    'מק"ט': "100101",
                    "תאור מוצר": date_str,
                    "כמות": round(9.00 - decimal_qty, 2),
                    "עבודה מהבית?": "Y",
                })

        elif not is_weekend:
            # יום חול ללא דיווח נוכחות כלל -> 9 שעות מלאות מהבית
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


def call_interactions(key: str, body: dict, timeout: int = 120):
    """מחזיר (success, data_or_error_dict)."""
    res = requests.post(
        INTERACTIONS_URL, headers=auth_headers(key), json=body, timeout=timeout
    )
    try:
        data = res.json()
    except ValueError:
        return False, {"message": f"תגובה לא תקינה מהשרת (HTTP {res.status_code})"}

    if res.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        return False, {
            "code": err.get("code", res.status_code),
            "message": err.get("message", str(data)),
        }
    return True, data


# ---------------------------------------------------------------------------
# בדיקת חיבור
# ---------------------------------------------------------------------------
with st.expander("🔧 בדיקת חיבור"):
    st.caption(
        "האפליקציה משתמשת ב-Interactions API. הנתיב הישן "
        "models/{model}:generateContent אינו תומך במפתחות AQ. ומחזיר 401."
    )
    if st.button("שלח בקשת בדיקה"):
        if not user_api_key.strip():
            st.error("אנא הזן מפתח API.")
        else:
            ok, result = call_interactions(
                user_api_key,
                {"model": MODELS_TO_TRY[0], "input": "החזר את המילה תקין בלבד"},
                timeout=60,
            )
            if ok:
                st.success(f"החיבור תקין. תגובת המודל: {extract_output_text(result)}")
            else:
                st.error(f"שגיאה {result.get('code')}: {result.get('message')}")


# ---------------------------------------------------------------------------
# העלאה ועיבוד
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader("צלם או העלה תמונה של הדו\"ח:", type=["jpg", "jpeg", "png"])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="התמונה שהועלתה", use_container_width=True)

    if st.button("🚀 עבד והפק אקסל", use_container_width=True):
        if not user_api_key.strip():
            st.error("אנא ודא שמפתח ה-API מוזן בשדה למעלה.")
        else:
            with st.spinner("מפענח את התמונה ומחשב נתונים..."):
                try:
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
                    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

                    result_data = None
                    last_error = ""

                    for model_name in MODELS_TO_TRY:
                        body = {
                            "model": model_name,
                            # הטקסט לפני התמונה — המלצת גוגל לדיוק טוב יותר
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
                        }

                        stop_all = False
                        for attempt in range(2):
                            ok, result = call_interactions(user_api_key, body)
                            if ok:
                                result_data = result
                                break

                            code = result.get("code")
                            last_error = f"{code}: {result.get('message')}"

                            if code in (401, 403):
                                stop_all = True
                                break
                            if code == 404:
                                break  # מודל לא זמין -> נסה את הבא
                            if code in (429, 500, 503):
                                time.sleep(2)
                                continue
                            break

                        if result_data or stop_all:
                            break

                    if not result_data:
                        st.error(f"שגיאת תקשורת מול גוגל: {last_error}")
                        if last_error.startswith(("401", "403")):
                            st.info(
                                "אם השגיאה היא ACCESS_TOKEN_TYPE_UNSUPPORTED, ודא שהמפתח לא נמחק "
                                "ושהוא נוצר בפרויקט פעיל ב-https://aistudio.google.com/apikey"
                            )
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
