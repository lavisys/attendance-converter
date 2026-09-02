import streamlit as st
import pandas as pd
import math
import datetime
import requests
import base64
import io
from PIL import Image
import json

st.set_page_config(page_title="ממיר דוח נוכחות", page_icon="📅", layout="centered")

st.title("📅 ממיר דוח נוכחות לאקסל")
st.write("העלה תמונה של הדו\"ח וקבל קובץ אקסל מעובד ומאוזן לפי 9 שעות יומית.")

# הדבק כאן את המפתח החדש מ-AI Studio (חייב להתחיל ב-AIzaSy)
DEFAULT_API_KEY = "AIzaSy_YOUR_ACTUAL_KEY_HERE"
user_api_key = st.text_input("מפתח Google API:", value=DEFAULT_API_KEY, type="password")

uploaded_file = st.file_uploader("צלם או העלה תמונה של הדו\"ח:", type=["jpg", "jpeg", "png"])

def process_attendance_data(raw_days):
    processed_rows = []
    
    for item in raw_days:
        date_str = item.get("date", "")
        is_worked = item.get("worked", False)
        day_name = item.get("day", "")
        gross_hhmm = item.get("gross", None)
        
        is_weekend = day_name in ["ו", "שב", "שישי", "שבת"]
        
        if is_worked and gross_hhmm:
            try:
                parts = str(gross_hhmm).split(":")
                h, m = int(parts[0]), int(parts[1])
                total_min = h * 60 + m
                
                # עיגול לרבע השעה הקרובה
                rounded_min = round(total_min / 15.0) * 15
                decimal_qty = rounded_min / 60.0
            except:
                decimal_qty = 0.0
                
            processed_rows.append({
                'מק"ט': '100101',
                'תאור מוצר': date_str,
                'כמות': decimal_qty,
                'עבודה מהבית?': ''
            })
            
            # השלמה ל-9 שעות יומית בימי חול
            if not is_weekend and decimal_qty < 9.00:
                completion_qty = round(9.00 - decimal_qty, 2)
                processed_rows.append({
                    'מק"ט': '100101',
                    'תאור מוצר': date_str,
                    'כמות': completion_qty,
                    'עבודה מהבית?': 'Y'
                })
                
        elif not is_weekend:
            processed_rows.append({
                'מק"ט': '100101',
                'תאור מוצר': date_str,
                'כמות': 9.00,
                'עבודה מהבית?': 'Y'
            })
            
    return pd.DataFrame(processed_rows)

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="התמונה שהועלתה", use_container_width=True)
    
    if st.button("🚀 עבד והפק אקסל", use_container_width=True):
        api_key_clean = user_api_key.strip()
        if not api_key_clean or not api_key_clean.startswith("AIzaSy"):
            st.error("אנא הכנס מפתח API תקין מ-Google AI Studio (חייב להתחיל ב-AIzaSy).")
        else:
            with st.spinner("מפענח את התמונה ומחשב נתונים..."):
                try:
                    buffered = io.BytesIO()
                    image.convert("RGB").save(buffered, format="JPEG")
                    img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    
                    headers = {"Content-Type": "application/json"}
                    
                    prompt = """
                    חלץ מתמונת דוח הנוכחות את כל הימים בחודש.
                    החזר JSON בלבד (ללא markdown וללא תגיות קוד) במבנה הבא:
                    [
                      {"date": "01/08/26", "day": "שב", "worked": false, "gross": null},
                      {"date": "05/08/26", "day": "ד", "worked": true, "gross": "06:22"}
                    ]
                    """
                    
                    payload = {
                        "contents": [
                            {
                                "parts": [
                                    {"text": prompt},
                                    {
                                        "inline_data": {
                                            "mime_type": "image/jpeg",
                                            "data": img_b64
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                    
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key_clean}"
                    res = requests.post(url, headers=headers, json=payload)
                    res_json = res.json()
                    
                    if "candidates" in res_json and len(res_json["candidates"]) > 0:
                        text_response = res_json['candidates'][0]['content']['parts'][0]['text']
                        clean_json = text_response.replace("```json", "").replace("```", "").strip()
                        raw_data = json.loads(clean_json)
                        
                        df = process_attendance_data(raw_data)
                        output_path = "attendance_summary.xlsx"
                        df.to_excel(output_path, index=False)
                        
                        st.success("העיבוד הושלם בהצלחה!")
                        st.dataframe(df)
                        
                        with open(output_path, "rb") as file:
                            st.download_button(
                                label="📥 הורד קובץ אקסל",
                                data=file,
                                file_name=f"attendance_{datetime.date.today()}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
                    else:
                        st.error(f"שגיאה מתגובת גוגל: {res_json}")
                except Exception as e:
                    st.error(f"שגיאה בעיבוד: {e}")
