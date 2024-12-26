import streamlit as st
from datetime import date, timedelta
import os
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from pydantic import BaseModel
from typing import List, Optional
import json
import pandas as pd

# Load environment variables
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
# Global nutrition targets
DAILY_TARGETS = {
    'calories': 2410,
    'protein': 180,
    'fat': 70,
    'carbs': 250,
}


# Data Models
class Meal(BaseModel):
    meal: str
    count: float
    calories: int
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbohydrates: Optional[float] = None

class MealListWithTotalCalories(BaseModel):
    meals: List[Meal]
    totalCalories: int

def parse_daily_meals(daily_string: str) -> MealListWithTotalCalories:
    """Parse a daily food log into structured meal data"""
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": """You are a helpful assistant. A user will provide you a list of foods they ate for the day. 
                You will take this and break down everything they ate into meals. For each meal:
                1. If calories were provided, use those
                2. If not, estimate calories based on typical portions
                3. Estimate grams of protein, fat, and carbohydrates
                4. Include reasonable calorie/macronutrient count guesses
                5. Calculate totalCalories as the sum of all meals"""
            },
            {"role": "user", "content": f"Parse the following daily food intake into structured meal data: {daily_string}"}
        ],
        functions=[
            {
                "name": "parse_meals",
                "description": "Parse meals from the given text",
                "parameters": MealListWithTotalCalories.schema()
            }
        ],
        function_call={"name": "parse_meals"}
    )

    function_call = completion.choices[0].message.function_call
    meals_data = eval(function_call.arguments)
    return MealListWithTotalCalories(**meals_data)

def edit_meals(original_query: str, meals_string: str, edit_query: str) -> MealListWithTotalCalories:
    """Edit existing meals based on new input"""
    combined_query = f"{original_query}\n\nHere's the current meal list:\n{meals_string}\nEdit this meal list with the user requested edits from this query: \n{edit_query}"
    return parse_daily_meals(combined_query)

def save_meals(meals: List[Meal], entry_date: date):
    """Save meals to Supabase"""
    try:
        # First check if date exists
        response = supabase.table('days')\
            .select("id")\
            .eq('date', entry_date.isoformat())\
            .execute()
        
        if not response.data:
            # Create new day entry
            day_response = supabase.table('days')\
                .insert({"date": entry_date.isoformat()})\
                .execute()
            day_id = day_response.data[0]['id']
        else:
            day_id = response.data[0]['id']
        
        # Insert meals
        meals_data = [{
            "day_id": day_id,
            **meal.dict()
        } for meal in meals]
        
        response = supabase.table('meals')\
            .insert(meals_data)\
            .execute()
        
        return response.data
    except Exception as e:
        st.error(f"Error saving meals: {str(e)}")
        return None

def analyze_meal_macros(meal, DAILY_TARGETS):
    """
    Dynamic analysis based on DAILY_TARGETS values.
    Returns color-coded evaluation of each macro based on proportions and ratios.
    """
    # Calculate percentages of daily targets
    cal_percent = (meal['calories'] / DAILY_TARGETS['calories']) * 100
    
    # Calculate target macro ratios in terms of calories
    target_cals = DAILY_TARGETS['calories']
    target_protein_cals = (DAILY_TARGETS['protein'] * 4)  # 30% of calories
    target_fat_cals = (DAILY_TARGETS['fat'] * 9)         # 26% of calories
    target_carb_cals = (DAILY_TARGETS['carbs'] * 4)      # 44% of calories
    
    # Calculate actual macro ratios for this meal
    meal_protein_cals = meal['protein'] * 4
    meal_fat_cals = meal['fat'] * 9
    meal_carb_cals = meal['carbohydrates'] * 4
    
    # Convert to percentages of meal calories
    if meal['calories'] > 0:
        protein_cal_ratio = (meal_protein_cals / meal['calories']) * 100
        fat_cal_ratio = (meal_fat_cals / meal['calories']) * 100
        carb_cal_ratio = (meal_carb_cals / meal['calories']) * 100
    else:
        protein_cal_ratio = fat_cal_ratio = carb_cal_ratio = 0
    
    # Target ratios
    target_protein_ratio = (target_protein_cals / target_cals) * 100  # ~30%
    target_fat_ratio = (target_fat_cals / target_cals) * 100         # ~26%
    target_carb_ratio = (target_carb_cals / target_cals) * 100      # ~44%
    
    evaluations = {
        'calories': {
            'percent': cal_percent,
            'color': '#ff9999' if cal_percent > 35 else '#666666' if cal_percent > 20 else '#99cc99',
            'direction': '↑' if cal_percent > 35 else None if 20 <= cal_percent <= 35 else '↓'
        },
        'protein': {
            'percent': protein_cal_ratio,
            'color': '#ff9999' if protein_cal_ratio < (target_protein_ratio * 0.67) else 
                    '#666666' if protein_cal_ratio < target_protein_ratio else '#99cc99',
            'direction': '↓' if protein_cal_ratio < (target_protein_ratio * 0.67) else None
        },
        'fat': {
            'percent': fat_cal_ratio,
            'color': '#ff9999' if fat_cal_ratio > (target_fat_ratio * 1.4) else 
                    '#666666' if fat_cal_ratio > target_fat_ratio else '#99cc99',
            'direction': '↑' if fat_cal_ratio > (target_fat_ratio * 1.4) else None
        },
        'carbs': {
            'percent': carb_cal_ratio,
            'color': '#ff9999' if carb_cal_ratio > (target_carb_ratio * 1.4) else 
                    '#666666' if carb_cal_ratio > target_carb_ratio else '#99cc99',
            'direction': '↑' if carb_cal_ratio > (target_carb_ratio * 1.4) else None
        }
    }
    
    return evaluations

# Streamlit UI
st.title("Meal Logger")

# Tab for creating/editing meals
tab1, tab2, tab3 = st.tabs(["Log Meals", "View History", "Chat"])

with tab1:
    # Input form
    with st.form("meal_form"):
        log_text = st.text_area(
            "Enter your food log",
            placeholder="Write everything you ate for the day like you're speaking to a friend"
        )
        
        entry_date = st.date_input(
            "Entry Date",
            value=date.today(),
            min_value=date.today() - timedelta(days=365),
            max_value=date.today() + timedelta(days=365)
        )
        
        preview_submit = st.form_submit_button("Preview Meals")
    
    # Process and show preview when submitted
    if preview_submit and log_text:
        # Store the initial log text in session state
        st.session_state['log_text'] = log_text
        
    # Check if we have a log to process
    if 'log_text' in st.session_state:
        try:
            # Process the meals
            if not 'current_meals' in st.session_state:
                result = parse_daily_meals(st.session_state['log_text'])
                st.session_state['current_meals'] = result
            
            # Convert meals to DataFrame for editing
            meals_df = pd.DataFrame([{
                'meal': meal.meal,
                'calories': meal.calories,
                'protein': meal.protein,
                'fat': meal.fat,
                'carbs': meal.carbohydrates,
                'count': meal.count,
            } for meal in st.session_state['current_meals'].meals])
            
            st.subheader("Processed Meals Preview:")
            edited_df = st.data_editor(
                meals_df,
                column_config={
                    "meal": st.column_config.TextColumn(
                        "Meal Name",
                        help="Description of the meal",
                        width=300,
                    ),
                    "calories": st.column_config.NumberColumn(
                        "Cal",
                        help="Total calories",
                        min_value=0,
                        format="%d",
                        width=70,
                    ),
                    "protein": st.column_config.NumberColumn(
                        "Protein",
                        help="Grams of protein",
                        min_value=0,
                        format="%.1fg",
                        width=70,
                    ),
                    "fat": st.column_config.NumberColumn(
                        "Fat",
                        help="Grams of fat",
                        min_value=0,
                        format="%.1fg",
                        width=70,
                    ),
                    "carbs": st.column_config.NumberColumn(
                        "Carbs",
                        help="Grams of carbohydrates",
                        min_value=0,
                        format="%.1fg",
                        width=70,
                    ),
                    "count": st.column_config.NumberColumn(
                        "×",
                        help="Number of servings",
                        min_value=0,
                        max_value=10,
                        step=0.5,
                        format="%.1f",
                        width=50,
                    ),
                },
                hide_index=True,
                disabled=["meal", "calories", "protein", "fat", "carbs", "count"],
                num_rows="fixed"
            )
            
            # Calculate and show totals using native Streamlit
            if not edited_df.empty:
                totals = edited_df.sum()
                st.text(f"Totals: {int(totals['calories'])} cal | {totals['protein']:.1f}g protein | {totals['fat']:.1f}g fat | {totals['carbs']:.1f}g carbs")
            
            # Update session state with edited values
            if edited_df is not None:
                st.session_state['current_meals'].meals = [
                    Meal(
                        meal=row['meal'],
                        count=row['count'],
                        calories=row['calories'],
                        protein=row['protein'],
                        fat=row['fat'],
                        carbohydrates=row['carbs']
                    ) for _, row in edited_df.iterrows()
                ]
                st.session_state['current_meals'].totalCalories = int(edited_df['calories'].sum())
            
            # Always show modification section
            st.subheader("Need to modify?")
            edit_query = st.text_area(
                "Enter your modifications here",
                placeholder="Example: Change the calories of the first meal to 500, Add 2 eggs to breakfast",
                key="edit_area"
            )
            
            col1, col2 = st.columns(2)
            
            if col1.button("Modify"):
                if edit_query:
                    with st.spinner("Updating..."):
                        modified_result = edit_meals(
                            original_query=st.session_state['log_text'],
                            meals_string=json.dumps(st.session_state['current_meals'].dict()),
                            edit_query=edit_query
                        )
                        st.session_state['current_meals'] = modified_result
                        st.rerun()
            
            if col2.button("Save"):
                with st.spinner("Saving..."):
                    if st.session_state.get('current_meals'):
                        save_meals(st.session_state['current_meals'].meals, entry_date)
                        st.success("Meals saved successfully!")
                        # Clear the session state
                        del st.session_state['current_meals']
                        del st.session_state['log_text']
                        st.rerun()
                    
        except Exception as e:
            st.error(f"Error: {str(e)}")

with tab2:
    # Display daily targets in a compact table with smaller text
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("<div style='font-size: 0.8em;'>Daily Calories</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{DAILY_TARGETS['calories']}</div>", unsafe_allow_html=True)
    with col2:
        st.markdown("<div style='font-size: 0.8em;'>Protein Target</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{DAILY_TARGETS['protein']}g</div>", unsafe_allow_html=True)
    with col3:
        st.markdown("<div style='font-size: 0.8em;'>Fat Target</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{DAILY_TARGETS['fat']}g</div>", unsafe_allow_html=True)
    with col4:
        st.markdown("<div style='font-size: 0.8em;'>Carbs Target</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{DAILY_TARGETS['carbs']}g</div>", unsafe_allow_html=True)

    # Display meal history
    st.subheader("Meal History")
    
    try:
        # First get all days
        days_response = supabase.table('days')\
            .select("*")\
            .order('date', desc=True)\
            .execute()

        for day in days_response.data:
            # Get meals for each day
            meals_response = supabase.table('meals')\
                .select("*")\
                .eq('day_id', day['id'])\
                .execute()
            
            # Calculate daily totals
            total_calories = sum(meal['calories'] for meal in meals_response.data)
            total_protein = sum(meal.get('protein', 0) or 0 for meal in meals_response.data)
            total_fat = sum(meal.get('fat', 0) or 0 for meal in meals_response.data)
            total_carbs = sum(meal.get('carbohydrates', 0) or 0 for meal in meals_response.data)
            
            # Calculate differences from targets
            cal_diff = total_calories - DAILY_TARGETS['calories']
            protein_diff = total_protein - DAILY_TARGETS['protein']
            fat_diff = total_fat - DAILY_TARGETS['fat']
            carbs_diff = total_carbs - DAILY_TARGETS['carbs']
            
            # Create a row with columns for the expander, nutrition summary, and delete button
            cols = st.columns([3.5, 1.5, 0.5])
            with cols[0]:
                with st.expander(f"Meals for {day['date']}"):
                    # Sort meals by calories in descending order
                    meals = sorted(meals_response.data, key=lambda x: x['calories'], reverse=True)
                    
                    for meal in meals:
                        # Get macro analysis
                        analysis = analyze_meal_macros(meal, DAILY_TARGETS)
                        
                        # Show meal name and delete button in same row
                        meal_row = st.columns([0.9, 0.1])
                        meal_row[0].markdown(f"""
                            <div style='margin: 0; padding: 0; line-height: 1; display: flex; align-items: center; min-height: 32px;'>• {meal['meal']} (x{meal['count']})</div>
                            """, unsafe_allow_html=True)
                        if meal_row[1].button("🗑️", key=f"delete_meal_{meal['id']}", help=""):
                            with st.spinner("Deleting meal..."):
                                supabase.table('meals')\
                                    .delete()\
                                    .eq('id', meal['id'])\
                                    .execute()
                                st.rerun()
                        
                        # Show nutritional info with color coding
                        st.markdown(f"""
                            <div style='margin: 0; padding: 0; line-height: 1; font-size: 0.9em; display: flex; align-items: center; min-height: 24px;'>
                                Cal: <span style='color: {analysis['calories']['color']}'>{meal['calories']}{analysis['calories']['direction'] or ''}</span> | 
                                Protein: <span style='color: {analysis['protein']['color']}'>{meal['protein']}g{analysis['protein']['direction'] or ''}</span> | 
                                Fat: <span style='color: {analysis['fat']['color']}'>{meal['fat']}g{analysis['fat']['direction'] or ''}</span> | 
                                Carbs: <span style='color: {analysis['carbs']['color']}'>{meal['carbohydrates']}g{analysis['carbs']['direction'] or ''}</span>
                            </div>
                            <hr style='margin: 3px 0; padding: 0;'>
                            """, unsafe_allow_html=True)
            
            # Show nutrition summary with differences
            with cols[1]:
                # Calculate differences and determine colors/arrows using same logic as meals
                cal_color = '#ff9999' if total_calories > (DAILY_TARGETS['calories'] * 1.1) else '#99cc99'
                protein_color = '#ff9999' if total_protein < (DAILY_TARGETS['protein'] * 0.67) else '#666666' if total_protein < DAILY_TARGETS['protein'] else '#99cc99'
                fat_color = '#ff9999' if total_fat > (DAILY_TARGETS['fat'] * 1.4) else '#666666' if total_fat > DAILY_TARGETS['fat'] else '#99cc99'
                carbs_color = '#ff9999' if total_carbs > (DAILY_TARGETS['carbs'] * 1.4) else '#666666' if total_carbs > DAILY_TARGETS['carbs'] else '#99cc99'
                
                # Add arrows based on the same thresholds
                cal_arrow = '↑' if total_calories > (DAILY_TARGETS['calories'] * 1.1) else ''
                protein_arrow = '↓' if total_protein < (DAILY_TARGETS['protein'] * 0.67) else ''
                fat_arrow = '↑' if total_fat > (DAILY_TARGETS['fat'] * 1.4) else ''
                carbs_arrow = '↑' if total_carbs > (DAILY_TARGETS['carbs'] * 1.4) else ''
                
                st.markdown(f"""
                    <div style='font-size: 0.9em; line-height: 1.2;'>
                        <div>Cal: <span style='color: {cal_color}'>{total_calories}{cal_arrow}</span></div>
                        <div style='font-size: 0.8em; color: #666;'>
                            Protein: <span style='color: {protein_color}'>{total_protein}g{protein_arrow}</span><br>
                            Fat: <span style='color: {fat_color}'>{total_fat}g{fat_arrow}</span><br>
                            Carbs: <span style='color: {carbs_color}'>{total_carbs}g{carbs_arrow}</span>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
            
            # Delete day button
            if cols[2].button("🗑️", key=f"delete_day_{day['id']}", help=""):
                with st.spinner("Deleting day..."):
                    supabase.table('meals')\
                        .delete()\
                        .eq('day_id', day['id'])\
                        .execute()
                    supabase.table('days')\
                        .delete()\
                        .eq('id', day['id'])\
                        .execute()
                    st.rerun()
    except Exception as e:
        st.error(f"Error fetching meal history: {str(e)}")

with tab3:
    st.subheader("Nutrition Assistant")
    
    # Initialize chat history in session state if not exists
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    
    # Display chat history
    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("data") is not None:
                st.dataframe(message["data"])
    
    # Chat input and date selector side by side
    cols = st.columns([4, 1])
    with cols[0]:
        user_input = st.chat_input("Ask a question or log your meals...")
    with cols[1]:
        selected_date = st.date_input("Date", value=date.today())
    
    if user_input:
        # Add user message to chat
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        
        # Classify intent
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "system",
                "content": "Classify if this is a meal logging action or a question about nutrition/history."
            }, {
                "role": "user",
                "content": user_input
            }],
            functions=[{
                "name": "classify_intent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string", "enum": ["log_meal", "question"]}
                    }
                }
            }],
            function_call={"name": "classify_intent"}
        )
        
        intent = json.loads(completion.choices[0].message.function_call.arguments)["intent"]
        
        if intent == "log_meal":
            # Parse meals
            result = parse_daily_meals(user_input)
            
            # Convert to DataFrame for display
            meals_df = pd.DataFrame([{
                'meal': meal.meal,
                'calories': meal.calories,
                'protein': meal.protein,
                'fat': meal.fat,
                'carbs': meal.carbohydrates,
                'count': meal.count,
            } for meal in result.meals])
            
            # Add assistant response with DataFrame
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": "Here's what I understood from your meal log. Does this look correct?",
                "data": meals_df
            })
            
        else:  # question intent
            # Get historical context
            days_response = supabase.table('days')\
                .select("*")\
                .order('date', desc=True)\
                .limit(7)\
                .execute()
            
            meals_history = []
            for day in days_response.data:
                meals_response = supabase.table('meals')\
                    .select("*")\
                    .eq('day_id', day['id'])\
                    .execute()
                meals_history.extend(meals_response.data)
            
            # Generate response with context
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[{
                    "role": "system",
                    "content": f"""You are a nutrition assistant. Answer questions using this context:
                    Daily Targets: {json.dumps(DAILY_TARGETS)}
                    Recent meals: {json.dumps(meals_history)}"""
                }, {
                    "role": "user",
                    "content": user_input
                }]
            )
            
            # Add assistant response
            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": completion.choices[0].message.content
            })
        
        # Rerun to update chat display
        st.rerun()