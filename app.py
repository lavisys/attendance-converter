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

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# רשימת מודלים פעילים (gemini-2.0-flash הושבת ב-01/06/2026)
MODELS_TO_TRY = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]

# ---------------------------------------------------------------------------
# ניהול אישורים
# ---------------------------------------------------------------------------
# מומלץ להגדיר את המפתח ב-.streamlit/secrets.toml בשורה:  GOOGLE_API_KEY = "AIza..."
default_key = ""
try:
    default_key = st.secrets.get("GOOGLE_API_KEY", "")
except Exception:
    default_key = ""

user_api_key = st.text_input(
    "מפתח Google API (מתחיל ב-AIza):",
    value=default_key,
    type="password",
    help="צור מפתח בכתובת https://aistudio.google.com/apikey . טוקן OAuth שמתחיל ב-AQ. אינו מפתח API.",
)


def build_auth(key: str):
    """מחזיר (headers, query_params) לפי סוג האישור שהוזן."""
    key = key.strip()
    headers = {"Content-Type": "application/json"}
    params = {}
    if key.startswith("AIza"):
        params["key"] = key
    else:
        # טוקן OAuth (AQ. / ya29.) חייב לעבור בכותרת Authorization ולא בפרמטר key
        headers["Authorization"] = f"Bearer {key}"
    return headers, params


def key_kind(key: str) -> str:
    key = key.strip()
    if not key:
        return "empty"
    if key.startswith("AIza"):
        return "api_key"
    if key.startswith(("AQ.", "ya29.")):
        return "oauth"
    return "unknown"


# ---------------------------------------------------------------------------
# עיבוד הנתונים
# ---------------------------------------------------------------------------
WEEKEND_NAMES = {"ו", "שב", "שישי", "שבת", "ש"}


def process_attendance_data(raw_days):
    processed_rows = []

    for item in raw_days:
        date_str = item.get("date", "")
        is_worked = bool(item.get("worked", False))
        day_name = str(item.get("day", "")).strip()
        gross_hhmm = item.get("gross", None)

        is_weekend = day_name in WEEKEND_NAMES

        if is_worked and gross_hhmm:
            try:
                parts = str(gross_hhmm).split(":")
                h, m = int(parts[0]), int(parts[1])
                total_min = h * 60 + m
                # עיגול לרבע השעה הקרובה
                rounded_min = round(total_min / 15.0) * 15
                decimal_qty = round(rounded_min / 60.0, 2)
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
                completion_qty = round(9.00 - decimal_qty, 2)
                processed_rows.append({
                    'מק"ט': "100101",
                    "תאור מוצר": date_str,
                    "כמות": completion_qty,
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


def extract_json(text: str):
    """מנקה ```json ומחלץ את מערך ה-JSON גם אם יש טקסט מסביב."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="נוכחות")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# בדיקת חיבור
# ---------------------------------------------------------------------------
with st.expander("🔧 בדיקת חיבור ומודלים זמינים"):
    kind = key_kind(user_api_key)
    if kind == "oauth":
        st.warning(
            "האישור שהוזן נראה כמו טוקן OAuth (מתחיל ב-AQ. או ya29.) ולא כמפתח API. "
            "הוא יישלח בכותרת Authorization, אך תוקפו פג תוך כשעה. "
            "מומלץ ליצור מפתח קבוע ב-https://aistudio.google.com/apikey"
        )
    elif kind == "unknown" and user_api_key.strip():
        st.warning("פורמט המפתח לא מזוהה. מפתח API תקין מתחיל ב-AIza.")

    if st.button("בדוק אילו מודלים זמינים"):
        if not user_api_key.strip():
            st.error("אנא הזן מפתח API.")
        else:
            headers, params = build_auth(user_api_key)
            try:
                r = requests.get(f"{API_BASE}/models", headers=headers, params=params, timeout=30)
                data = r.json()
                if "models" in data:
                    names = [
                        m["name"].replace("models/", "")
                        for m in data["models"]
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                    ]
                    st.success("החיבור תקין. מודלים שתומכים ב-generateContent:")
                    st.write(names)
                else:
                    st.error(f"תגובת שגיאה מגוגל: {data}")
            except Exception as e:
                st.error(f"שגיאה בבדיקה: {e}")


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
                    # הקטנת תמונות ענקיות כדי לחסוך זמן וטוקנים
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

                    prompt = (
                        "חלץ מתמונת דוח הנוכחות את כל הימים בחודש.\n"
                        "החזר מערך JSON בלבד, ללא markdown וללא טקסט נוסף, במבנה הבא:\n"
                        '[\n'
                        '  {"date": "01/08/26", "day": "שב", "worked": false, "gross": null},\n'
                        '  {"date": "05/08/26", "day": "ד", "worked": true, "gross": "06:22"}\n'
                        ']\n'
                        'השדה day הוא אות היום בעברית כפי שמופיע בדוח. '
                        'השדה gross הוא סך השעות ברוטו בפורמט HH:MM, או null אם אין דיווח.'
                    )

                    payload = {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": prompt},
                                    {
                                        "inline_data": {
                                            "mime_type": "image/jpeg",
                                            "data": img_b64,
                                        }
                                    },
                                ],
                            }
                        ],
                        "generationConfig": {
                            "temperature": 0,
                            "response_mime_type": "application/json",
                        },
                    }

                    headers, params = build_auth(user_api_key)

                    res_data = None
                    success = False
                    last_error_msg = ""

                    for model_name in MODELS_TO_TRY:
                        url = f"{API_BASE}/models/{model_name}:generateContent"

                        for attempt in range(2):
                            try:
                                res = requests.post(
                                    url, headers=headers, params=params,
                                    json=payload, timeout=120,
                                )
                                res_json = res.json()
                            except Exception as req_err:
                                last_error_msg = str(req_err)
                                time.sleep(1.5)
                                continue

                            if res_json.get("candidates"):
                                res_data = res_json
                                success = True
                                break

                            err = res_json.get("error", {})
                            code = err.get("code")
                            last_error_msg = err.get("message", str(res_json))

                            if code == 401 or code == 403:
                                # בעיית אישור — אין טעם לנסות מודלים נוספים
                                success = False
                                MODELS_TO_TRY = []
                                break
                            if code == 404:
                                break  # מודל לא קיים -> עבור למודל הבא
                            if code in (429, 500, 503):
                                time.sleep(2)
                                continue
                            break

                        if success or not MODELS_TO_TRY:
                            break

                    if not success:
                        st.error(f"שגיאת תקשורת מול גוגל: {last_error_msg}")
                        if "authentication" in last_error_msg.lower() or "API key" in last_error_msg:
                            st.info(
                                "נראה שהאישור אינו מפתח API תקין. "
                                "צור מפתח חדש בכתובת https://aistudio.google.com/apikey "
                                "(המפתח אמור להתחיל ב-AIza)."
                            )
                    else:
                        text_response = res_data["candidates"][0]["content"]["parts"][0]["text"]
                        raw_data = extract_json(text_response)

                        df = process_attendance_data(raw_data)

                        st.success("העיבוד הושלם בהצלחה!")
                        st.dataframe(df, use_container_width=True)

                        total = df["כמות"].sum() if not df.empty else 0
                        st.caption(f"סה\"כ שעות בדוח: {total:.2f}")

                        st.download_button(
                            label="📥 הורד קובץ אקסל",
                            data=to_excel_bytes(df),
                            file_name=f"attendance_{datetime.date.today()}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )

                except Exception as e:
                    st.error(f"שגיאה בעיבוד: {e}")
