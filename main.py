import streamlit as st
from datetime import date, timedelta
import os
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

# Load environment variables or secrets
if os.path.exists(".env"):
    load_dotenv()
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
else:
    OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Initialize clients
client = OpenAI(api_key=OPENAI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Models and processing functions (from your FastAPI app)
class NutritionEntry(BaseModel):
    log_string: str
    calories: float
    protein: float
    carbohydrates: float
    fats: float
    fiber: float
    sugar: float
    weight: Optional[float] = None
    bmi: Optional[float] = None
    sleep: Optional[float] = None

def process_log_entry(log: str) -> NutritionEntry:
    """
    Convert a natural language log into structured nutrition data
    """
    system_prompt = """You are a nutrition expert that analyzes food and activity logs.
    For any foods mentioned, estimate their nutritional content based on typical portions.
    When weight is mentioned, store it in pounds (lbs).

    Common Portions and Values:
    - Chicken breast (1 serving, 6oz): 280 calories, 53g protein, 0g carbs, 6g fat
    - Rice (1 cup cooked): 205 calories, 4g protein, 45g carbs, 0.4g fat
    - Eggs (1 large): 70 calories, 6g protein, 0.6g carbs, 5g fat
    - Bread (1 slice): 80 calories, 3g protein, 15g carbs, 1g fat
    - Banana (medium): 105 calories, 1.3g protein, 27g carbs (14g sugar), 0.4g fat
    - Milk (1 cup): 120 calories, 8g protein, 12g carbs (12g sugar), 5g fat

    If weight is given in kg, convert it to pounds (1 kg = 2.20462 lbs).
    Always store weight in pounds in the response.
    """

    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Please analyze this food and activity log: {log}"}
            ],
            response_format=NutritionEntry
        )
        
        return completion.choices[0].message.parsed

    except Exception as e:
        print(f"Error processing log: {str(e)}")
        return None

def save_or_update_entry(entry: NutritionEntry, entry_date: date) -> dict:
    """
    Save a new entry or update existing entry for the given date
    """
    try:
        response = supabase.table('entries')\
            .select("*")\
            .eq('date', entry_date.isoformat())\
            .execute()
        
        if not response.data:
            data = {
                "date": entry_date.isoformat(),
                **entry.model_dump()
            }
            response = supabase.table('entries')\
                .insert(data)\
                .execute()
            return response.data[0]
        
        else:
            existing_entry = response.data[0]
            combined_log = f"{existing_entry['log_string']}\n---\nAdditional entry: {entry.log_string}"
            
            updated_data = {
                "log_string": combined_log,
                "calories": round(existing_entry['calories'] + entry.calories, 1),
                "protein": round(existing_entry['protein'] + entry.protein, 1),
                "carbohydrates": round(existing_entry['carbohydrates'] + entry.carbohydrates, 1),
                "fats": round(existing_entry['fats'] + entry.fats, 1),
                "fiber": round(existing_entry['fiber'] + entry.fiber, 1),
                "sugar": round(existing_entry['sugar'] + entry.sugar, 1),
                "weight": entry.weight if entry.weight is not None else existing_entry['weight'],
                "sleep": entry.sleep if entry.sleep is not None else existing_entry['sleep'],
                "bmi": entry.bmi if entry.bmi is not None else existing_entry['bmi']
            }
            
            response = supabase.table('entries')\
                .update(updated_data)\
                .eq('date', entry_date.isoformat())\
                .execute()
            
            return response.data[0]

    except Exception as e:
        print(f"Error saving/updating entry: {str(e)}")
        return None

# Streamlit UI
st.title("Nutrition Logger")

# Display past entries
st.subheader("Past Entries")
try:
    response = supabase.table('entries')\
        .select("*")\
        .order('date', desc=True)\
        .execute()
    
    entries = response.data
    
    if entries:
        for entry in entries:
            with st.expander(f"Entry for {entry['date']}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Calories", f"{entry['calories']:.1f}")
                    st.metric("Protein", f"{entry['protein']:.1f}g")
                    st.metric("Carbs", f"{entry['carbohydrates']:.1f}g")
                with col2:
                    st.metric("Fats", f"{entry['fats']:.1f}g")
                    st.metric("Fiber", f"{entry['fiber']:.1f}g")
                    st.metric("Sugar", f"{entry['sugar']:.1f}g")
                
                if any(entry.get(metric) for metric in ['weight', 'bmi', 'sleep']):
                    metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
                    if entry.get('weight'):
                        metrics_col1.metric("Weight", f"{entry['weight']:.1f}lbs")
                    if entry.get('bmi'):
                        metrics_col2.metric("BMI", f"{entry['bmi']:.1f}")
                    if entry.get('sleep'):
                        metrics_col3.metric("Sleep", f"{entry['sleep']:.1f}hrs")
                
                st.text("Original log:")
                st.text(entry['log_string'])
except Exception as e:
    st.error(f"Error fetching entries: {str(e)}")

st.divider()

# Input form
with st.form("log_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        log_text = st.text_area(
            "Enter your food/activity log",
            placeholder="Example: I ate 2 eggs and a piece of toast for breakfast"
        )
    with col2:
        entry_date = st.date_input(
            "Entry Date",
            value=date.today(),
            min_value=date.today() - timedelta(days=365),
            max_value=date.today()
        )
    
    submit_button = st.form_submit_button("Submit Log")

    if submit_button and log_text:
        with st.spinner("Processing log..."):
            # Process the log
            entry = process_log_entry(log_text)
            if entry:
                # Save to database
                result = save_or_update_entry(entry, entry_date)
                if result:
                    st.success("Log submitted successfully!")
                    st.rerun()  # Refresh the page to show new entry