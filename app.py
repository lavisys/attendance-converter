import streamlit as st
import pandas as pd
import math
import datetime
import openpyxl
from google import genai
from google.genai import types
from PIL import Image
import json

st.set_page_config(page_title="ממיר דוח נוכחות", page_icon="📅", layout="centered")

st.title("📅 ממיר דוח נוכחות לאקסל")
st.write("העלה תמונה של הדו\"ח וקבל קובץ אקסל מעובד ישירות לטלפון.")

# הזנת מפתח API
api_key = st.text_input("הכנס מפתח Google API Key:", type="password")

uploaded_file = st.file_uploader("צלם או העלה תמונה:", type=["jpg", "jpeg", "png"])

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
                rounded_min = math.ceil(total_min / 15.0) * 15
                decimal_qty = rounded_min / 60.0
            except:
                decimal_qty = 0.0
                
            processed_rows.append({
                'מק"ט': '100101',
                'תאור מוצר': date_str,
                'כמות': decimal_qty,
                'עבודה מהבית?': ''
            })
        elif not is_weekend:
            processed_rows.append({
                'מק"ט': '100101',
                'תאור מוצר': date_str,
                'כמות': 9.00,
                'עבודה מהבית?': 'Y'
            })
            
    return pd.DataFrame(processed_rows)

if uploaded_file and api_key:
    image = Image.open(uploaded_file)
    st.image(image, caption="התמונה שהועלתה", use_container_width=True)
    
    if st.button("🚀 עבד והפק אקסל", use_container_width=True):
        with st.spinner("מפענח את התמונה ומחשב נתונים..."):
            try:
                client = genai.Client(api_key=api_key)
                
                prompt = """
                חלץ מתמונת דוח הנוכחות את כל הימים בחודש.
                החזר JSON בלבד (ללא markdown) במבנה הבא:
                [
                  {"date": "01/08/26", "day": "שב", "worked": false, "gross": null},
                  {"date": "05/08/26", "day": "ד", "worked": true, "gross": "06:22"}
                ]
                """
                
                response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[image, prompt]
                )
                
                clean_json = response.text.replace("```json", "").replace("```", "").strip()
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
            except Exception as e:
                st.error(f"שגיאה בעיבוד: {e}")
