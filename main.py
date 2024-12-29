import streamlit as st
from datetime import date, timedelta, datetime
import os
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client
from pydantic import BaseModel
from typing import List, Optional
import json
import pandas as pd
import pytz
from db import SupabaseRLS
from streamlit_javascript import st_javascript
import base64

import pandas as pd
import altair as alt
import pytz

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



# Data Models
class Meal(BaseModel):
    meal: str
    count: float
    calories: int
    protein: float
    fat: float
    carbohydrates: float

class MealListWithTotalCalories(BaseModel):
    meals: List[Meal]
    totalCalories: int

def parse_daily_meals(daily_string: str = "", image_data: bytes = None) -> MealListWithTotalCalories:
    """Parse a daily food log and/or meal image into structured meal data"""
    try:
        # Ensure we have either text or image
        if not daily_string and not image_data:
            raise ValueError("Must provide either text description or image")
        
        # Base system message
        messages = [
            {
                "role": "system",
                "content": """You are a helpful assistant. A user will provide you a list of foods they ate for the day and/or images of their meals. 
                You will take this and break down everything they ate into meals. For each meal:
                1. If calories were provided, use those
                2. If not, estimate calories based on typical portions
                3. Estimate grams of protein, fat, and carbohydrates
                4. Include reasonable calorie/macronutrient count guesses
                5. Calculate totalCalories as the sum of all meals
                
                Never return null or empty values for any of these fields including calories, protein, fat, or carbohydrates. If you need to estimate, make a reasonable guess."""
            }
        ]

        # Add content based on what was provided
        if image_data and daily_string:
            # Both image and text
            base64_image = base64.b64encode(image_data).decode('utf-8')
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Parse the following daily food intake and meal photo into structured meal data: {daily_string}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            })
        elif image_data:
            # Image only
            base64_image = base64.b64encode(image_data).decode('utf-8')
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this meal photo and provide the nutritional breakdown:"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            })
        else:
            # Text only
            messages.append({
                "role": "user",
                "content": f"Parse the following daily food intake into structured meal data: {daily_string}"
            })

        
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            functions=[
                {
                    "name": "parse_meals",
                    "description": "Parse meals from the given text and/or image",
                    "parameters": MealListWithTotalCalories.schema()
                }
            ],
            function_call={"name": "parse_meals"},
            max_tokens=500
        )

        function_call = completion.choices[0].message.function_call
        meals_data = eval(function_call.arguments)
        
        # Additional validation to ensure no null values
        result = MealListWithTotalCalories(**meals_data)
        for meal in result.meals:
            if any(v is None for v in meal.dict().values()):
                raise ValueError("Meal contains null values which are not allowed")
        return result
        
    except Exception as e:
        st.error(f"Error in parse_daily_meals: {str(e)}")
        raise

def edit_meals(original_query: str, meals_string: str, edit_query: str) -> MealListWithTotalCalories:
    """Edit existing meals based on new input"""
    combined_query = f"{original_query}\n\nHere's the current meal list:\n{meals_string}\nEdit this meal list with the user requested edits from this query: \n{edit_query}"
    return parse_daily_meals(combined_query)

def initialize_supabase_client():
    if st.session_state.authenticated and st.session_state.user:
        try:
            # Create a new client instance each time
            db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
            # Need to sign in with stored credentials to get proper authentication
            db.sign_in(st.session_state.user.email, st.session_state.user_password)
            return db
        except Exception as e:
            st.error(f"Failed to initialize database: {str(e)}")
            return None
    return None

def save_meals(meals: List[Meal], entry_date: date):
    """Save meals to Supabase"""
    try:
        db = initialize_supabase_client()
        if not db:
            raise Exception("Not authenticated")

        # First, check if day exists
        days_response = db.select_data(
            'days',
            '*',
            {'date': entry_date.isoformat(), 'user_id': st.session_state.user.id}
        )
        
        if not days_response:
            # Create new day
            day_response = db.insert_data('days', [{
                "date": entry_date.isoformat(),
                "user_id": st.session_state.user.id
            }])
            day_id = day_response[0]['id']
        else:
            day_id = days_response[0]['id']
        
        # Insert meals
        meals_data = [{
            "day_id": day_id,
            "user_id": st.session_state.user.id,
            **meal.dict()
        } for meal in meals]
        
        return db.insert_data('meals', meals_data)
    except Exception as e:
        st.error(f"Failed to save meals: {str(e)}")
        raise e

def analyze_meal_macros(meal, daily_targets):
    """
    Dynamic analysis based on daily_targets values.
    Returns color-coded evaluation of each macro based on proportions and ratios.
    """
    if not daily_targets:
        return {
            'calories': {'percent': 0, 'color': '#666666', 'direction': None},
            'protein': {'percent': 0, 'color': '#666666', 'direction': None},
            'fat': {'percent': 0, 'color': '#666666', 'direction': None},
            'carbs': {'percent': 0, 'color': '#666666', 'direction': None}
        }
        
    # Calculate percentages of daily targets
    cal_percent = (meal['calories'] / daily_targets['calories']) * 100
    
    # Calculate target macro ratios in terms of calories
    target_cals = daily_targets['calories']
    target_protein_cals = (daily_targets['protein'] * 4)  # 30% of calories
    target_fat_cals = (daily_targets['fat'] * 9)         # 26% of calories
    target_carb_cals = (daily_targets['carbs'] * 4)      # 44% of calories
    
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


# After your imports and before the main app code
def initialize_session_state():
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user' not in st.session_state:
        st.session_state.user = None

def login_page():
    st.title("Welcome!")
    
    # Get the full URL including hash
    url = st_javascript("await fetch('').then(r => window.parent.location.href)")
    
    # If we have a URL and it contains a hash, parse it
    if url and '#' in url:
        hash_part = url.split('#')[1]
        # Convert the hash parameters to a dictionary
        params = dict(param.split('=') for param in hash_part.split('&'))
        
        if params.get('type') == 'recovery':
            # Show password reset form
            st.subheader("Reset Your Password")
            with st.form("recovery_password_form"):
                new_password = st.text_input("New Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                submitted = st.form_submit_button("Update Password")
                
                if submitted:
                    if new_password != confirm_password:
                        st.error("Passwords don't match!")
                    elif len(new_password) < 6:
                        st.error("Password must be at least 6 characters long!")
                    else:
                        try:
                            db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
                            # Get both tokens from parsed parameters
                            access_token = params.get('access_token')
                            refresh_token = params.get('refresh_token')
                            
                            if not access_token or not refresh_token:
                                st.error("Link may have expired. Please request a new password reset link.")
                                return
                            
                            # Update the password with both tokens
                            db.update_password(
                                new_password, 
                                access_token=access_token,
                                refresh_token=refresh_token
                            )
                            st.success("Password updated successfully! You can now log in with your new password.")
                            st.rerun()
                        except Exception as e:
                            st.error("Link may have expired. Please request a new password reset link.")
            return  # Exit early, don't show other tabs

    # Regular login tabs for non-recovery flow
    tab_login, tab_signup, tab_reset = st.tabs(["Login", "Sign Up", "Reset Password"])

    with tab_login:
        st.subheader("Log In")
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted_login = st.form_submit_button("Login")
           
            if submitted_login:
                try:
                    db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
                    auth_response = db.sign_in(email, password)
                    st.session_state.authenticated = True
                    st.session_state.user = auth_response["user"]
                    # Store password for future db initializations
                    st.session_state.user_password = password
                    st.success(f"Logged in as {auth_response['user'].email}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {str(e)}")

    with tab_signup:
        st.subheader("Sign Up")
        with st.form("signup_form"):
            email_signup = st.text_input("Email", key="signup_email")
            password_signup = st.text_input("Password", type="password", key="signup_password")
            submitted_signup = st.form_submit_button("Sign Up")
            
            if submitted_signup:
                try:
                    db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
                    auth_response = db.sign_up(email_signup, password_signup)
                    st.success("Sign-up successful! Check your email to confirm your account.")
                except Exception as e:
                    st.error(f"Sign-up failed: {str(e)}")

    with tab_reset:
        st.subheader("Reset Password")
        
        if 'reset_email_sent' not in st.session_state:
            st.session_state.reset_email_sent = False
            
        if not st.session_state.reset_email_sent:
            # Show the initial password reset form
            with st.form("reset_form"):
                reset_email = st.text_input("Email", key="reset_email")
                submitted_reset = st.form_submit_button("Send Reset Link")
                
                if submitted_reset and reset_email:
                    try:
                        db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
                        db.request_password_reset(reset_email)
                        st.session_state.reset_email_sent = True
                        st.success("Password reset link sent! Please check your email.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to send reset link: {str(e)}")
        
        else:
            # Show the new password form after email is sent
            st.info("Please check your email for the password reset link. Once you've clicked it, you can set your new password here:")
            with st.form("new_password_form"):
                new_password = st.text_input("New Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                submitted_new_pw = st.form_submit_button("Update Password")
                
                if submitted_new_pw:
                    if new_password != confirm_password:
                        st.error("Passwords don't match!")
                    elif len(new_password) < 6:
                        st.error("Password must be at least 6 characters long!")
                    else:
                        try:
                            db = SupabaseRLS(SUPABASE_URL, SUPABASE_KEY)
                            db.update_password(new_password)
                            st.session_state.reset_email_sent = False
                            st.success("Password updated successfully! You can now log in with your new password.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to update password: {str(e)}")
            
            if st.button("Start Over"):
                st.session_state.reset_email_sent = False
                st.rerun()

def main():
    if 'error_info' in st.session_state:
        st.error("Previous Error:")
        st.json(st.session_state['error_info'])
        if st.button("Clear Error"):
            del st.session_state['error_info']
            del st.session_state['debug_info']
            st.rerun()
    

    initialize_session_state()
    
    # Show login page if not authenticated
    if not st.session_state.authenticated:
        login_page()
        return
    
    #st.write(st.session_state.user)
    # Create a small container for the logout button in the top right
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("Logout", type="secondary", use_container_width=True):
            try:
                db = initialize_supabase_client()
                if db:
                    db.sign_out()
                st.session_state.authenticated = False
                st.session_state.user = None
                st.rerun()
            except Exception as e:
                st.error(f"Logout failed: {str(e)}")
    with col1:
        st.title("Meal Logger")
    
    # Your existing app code goes here
    tab1, tab2, tab3 = st.tabs(["Log Meals", "View History", "Weekly Insights"])

    with tab1:
        # Get current Pacific time
        pacific = pytz.timezone('America/Los_Angeles')
        now = datetime.now(pacific)
        
        # If it's before 5am, use previous day's date
        entry_date_default = now.date() if now.hour >= 5 else (now - timedelta(days=1)).date()
        
        # Input form
        with st.form("meal_form"):
            log_text = st.text_area(
                "Enter your food log",
                placeholder="Write everything you ate for the day like you're speaking to a friend"
            )
            
            entry_date = st.date_input(
                "Entry Date",
                value=entry_date_default,
                min_value=date.today() - timedelta(days=365),
                max_value=date.today() + timedelta(days=365)
            )
            
            uploaded_file = st.file_uploader("(Optional) Take a photo of your meal", type=["jpg", "jpeg", "png"])
            
            image_data = None
            if uploaded_file is not None:
                
                # Convert image to bytes
                image_data = uploaded_file.getvalue()
                
                # Verify base64 encoding works
                try:
                    base64_test = base64.b64encode(image_data).decode('utf-8')
                except Exception as e:
                    st.error(f"Base64 encoding failed: {str(e)}")
            
            preview_submit = st.form_submit_button("Preview Meals")
        
        # Process and show preview when submitted
        st.session_state['log_text'] = None
        if preview_submit and log_text:
            # Store the initial log text in session state
            st.session_state['log_text'] = log_text
            
        # Check if we have a log to process
        if st.session_state['log_text'] or image_data:
            try:
                # Process the meals
                if not 'current_meals' in st.session_state:
                    result = parse_daily_meals(st.session_state['log_text'], image_data)
                    print(result)
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



    def get_user_targets(db):
        """Get targets for the current user"""
        try:
            targets = db.select_data(
                'targets',
                '*',
                match_dict={'user_id': st.session_state.user.id}
            )
            if targets:
                return {
                    'calories': targets[0]['calories'],
                    'protein': targets[0]['protein'],
                    'fat': targets[0]['fat'],
                    'carbs': targets[0]['carbs']
                }
            return None
        except Exception as e:
            st.error(f"Failed to get targets: {str(e)}")
            return None

    def save_user_targets(db, targets_dict):
        """Save or update targets for the current user"""
        try:
            existing_targets = db.select_data(
                'targets',
                '*',
                match_dict={'user_id': st.session_state.user.id}
            )
            
            data = {
                'user_id': st.session_state.user.id,
                **targets_dict
            }
            
            if existing_targets:
                # Update existing targets
                db.update_data('targets', 
                            match_dict={'user_id': st.session_state.user.id},
                            new_data=data)
            else:
                # Insert new targets
                db.insert_data('targets', data)
            return True
        except Exception as e:
            st.error(f"Failed to save targets: {str(e)}")
            return False

    with tab2:
        db = initialize_supabase_client()
        if not db:
            st.error("Please log in again")
            return

        user_targets = get_user_targets(db)
        
        # Display targets section
        st.subheader("Daily Targets")
        
        if 'editing_targets' not in st.session_state:
            st.session_state.editing_targets = False
        
        # Create two columns - one for targets, one for edit button
        col1, col2 = st.columns([4, 1])
        
        with col1:
            if not user_targets:
                # Show input form for new targets
                with st.form("targets_form"):
                    new_calories = st.number_input("Daily Calories", min_value=0, value=2000)
                    new_protein = st.number_input("Protein (g)", min_value=0, value=150)
                    new_fat = st.number_input("Fat (g)", min_value=0, value=70)
                    new_carbs = st.number_input("Carbs (g)", min_value=0, value=250)
                    
                    if st.form_submit_button("Save Targets"):
                        targets_dict = {
                            'calories': new_calories,
                            'protein': new_protein,
                            'fat': new_fat,
                            'carbs': new_carbs
                        }
                        if save_user_targets(db, targets_dict):
                            st.success("Targets saved!")
                            st.rerun()
            
            elif st.session_state.editing_targets:
                # Show edit form
                with st.form("edit_targets_form"):
                    edit_calories = st.number_input("Daily Calories", min_value=0, value=user_targets['calories'])
                    edit_protein = st.number_input("Protein (g)", min_value=0, value=user_targets['protein'])
                    edit_fat = st.number_input("Fat (g)", min_value=0, value=user_targets['fat'])
                    edit_carbs = st.number_input("Carbs (g)", min_value=0, value=user_targets['carbs'])
                    
                    if st.form_submit_button("Save Changes"):
                        targets_dict = {
                            'calories': edit_calories,
                            'protein': edit_protein,
                            'fat': edit_fat,
                            'carbs': edit_carbs
                        }
                        if save_user_targets(db, targets_dict):
                            st.success("Targets updated!")
                            st.session_state.editing_targets = False
                            st.rerun()
            
            else:
                # Display current targets
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.markdown("<div style='font-size: 0.8em;'>Daily Calories</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{user_targets['calories']}</div>", unsafe_allow_html=True)
                with col2:
                    st.markdown("<div style='font-size: 0.8em;'>Protein Target</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{user_targets['protein']}g</div>", unsafe_allow_html=True)
                with col3:
                    st.markdown("<div style='font-size: 0.8em;'>Fat Target</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{user_targets['fat']}g</div>", unsafe_allow_html=True)
                with col4:
                    st.markdown("<div style='font-size: 0.8em;'>Carbs Target</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size: 0.9em; font-weight: bold;'>{user_targets['carbs']}g</div>", unsafe_allow_html=True)
        
        with col2:
            if user_targets and not st.session_state.editing_targets:
                if st.button("Edit"):
                    st.session_state.editing_targets = True
                    st.rerun()
            elif user_targets and st.session_state.editing_targets:
                if st.button("Cancel"):
                    st.session_state.editing_targets = False
                    st.rerun()


        # Display meal history
        st.subheader("Meal History")
        print("hello world 1")
        try:
            print("hello world 3")
            db = initialize_supabase_client()
            print("hello world 4")
            if not db:
                st.error("Please log in again")
                return
            print("hello world 5")

            # Get all days for the user
            days_response = db.select_data(
                'days',
                '*',
                match_dict={'user_id': st.session_state.user.id},
                order_by={'column': 'date', 'ascending': False}
            )
            print("hello world 6")
            print("hello world")
            print(days_response)

            for day in days_response:
                # Get meals for each day
                meals_response = db.select_data(
                    'meals',
                    '*',
                    match_dict={
                        'day_id': day['id'],
                        'user_id': st.session_state.user.id
                    }
                )
                
                
                # Calculate daily totals with NULL handling
                total_calories = sum(meal.get('calories', 0) or 0 for meal in meals_response)
                total_protein = sum(meal.get('protein', 0) or 0 for meal in meals_response)
                total_fat = sum(meal.get('fat', 0) or 0 for meal in meals_response)
                total_carbs = sum(meal.get('carbohydrates', 0) or 0 for meal in meals_response)
                
                # Calculate differences from targets if they exist
                if user_targets:
                    cal_diff = total_calories - user_targets['calories']
                    protein_diff = total_protein - user_targets['protein']
                    fat_diff = total_fat - user_targets['fat']
                    carbs_diff = total_carbs - user_targets['carbs']
                else:
                    cal_diff = protein_diff = fat_diff = carbs_diff = 0
                
                # Create a row with columns for the expander, nutrition summary, and delete button
                cols = st.columns([3.5, 1.5, 0.5])
                with cols[0]:
                    with st.expander(f"Meals for {day['date']}"):
                        # Sort meals by calories in descending order
                        meals = sorted(meals_response, key=lambda x: x['calories'], reverse=True)
                        
                        for meal in meals:
                            # Safely get values with defaults for NULL
                            safe_meal = {
                                'meal': meal.get('meal', 'Unknown'),
                                'count': float(meal.get('count', 1) or 1),  # Default to 1 if NULL
                                'calories': int(meal.get('calories', 0) or 0),
                                'protein': float(meal.get('protein', 0) or 0),
                                'fat': float(meal.get('fat', 0) or 0),
                                'carbohydrates': float(meal.get('carbohydrates', 0) or 0)
                            }
                            
                            if user_targets:
                                analysis = analyze_meal_macros(safe_meal, user_targets)
                            else:
                                # Provide default analysis if no targets set
                                analysis = {
                                    'calories': {'color': '#666666', 'direction': None},
                                    'protein': {'color': '#666666', 'direction': None},
                                    'fat': {'color': '#666666', 'direction': None},
                                    'carbs': {'color': '#666666', 'direction': None}
                                }
                            
                            # Show meal name and delete button in same row
                            meal_row = st.columns([0.9, 0.1])
                            meal_row[0].markdown(f"""
                                <div style='margin: 0; padding: 0; line-height: 1; display: flex; align-items: center; min-height: 32px;'>• {meal['meal']} (x{meal['count']})</div>
                                """, unsafe_allow_html=True)
                            if meal_row[1].button("🗑️", key=f"delete_meal_{meal['id']}", help=""):
                                with st.spinner("Deleting meal..."):
                                    db.delete_data('meals', {'id': meal['id']})
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
                    if user_targets:
                        # Calculate differences and determine colors/arrows
                        cal_color = '#ff9999' if total_calories > (user_targets['calories'] * 1.1) else '#99cc99'
                        protein_color = '#ff9999' if total_protein < (user_targets['protein'] * 0.67) else '#666666' if total_protein < user_targets['protein'] else '#99cc99'
                        fat_color = '#ff9999' if total_fat > (user_targets['fat'] * 1.4) else '#666666' if total_fat > user_targets['fat'] else '#99cc99'
                        carbs_color = '#ff9999' if total_carbs > (user_targets['carbs'] * 1.4) else '#666666' if total_carbs > user_targets['carbs'] else '#99cc99'
                    else:
                        # Default colors if no targets set
                        cal_color = protein_color = fat_color = carbs_color = '#666666'

                    if user_targets:
                        cal_diff = total_calories - user_targets['calories']
                        if cal_diff < 0:
                            # Under target (good if trying to lose weight)
                            cal_remaining_color = '#99cc99'  # Light green
                            cal_remaining_symbol = '↓'  # Down arrow
                        elif cal_diff > 0:
                            # Over target
                            cal_remaining_color = '#ff9999'  # Light red
                            cal_remaining_symbol = '↑'  # Up arrow
                        else:
                            # Exactly at target
                            cal_remaining_color = '#666666'  # Gray
                            cal_remaining_symbol = '='  # Equals sign
                    else:
                        cal_remaining_color = '#666666'
                        cal_remaining_symbol = ''
                        cal_diff = 0

                    st.markdown(f"""
                        <div style='font-size: 0.9em; line-height: 1.2;'>
                            <div>Cal: <span style='color: {cal_color}'>{total_calories}</span>
                                <span style='color: {cal_remaining_color}; font-size: 0.85em; margin-left: 4px;'>
                                    {cal_remaining_symbol}{abs(cal_diff)}
                                </span>
                            </div>
                            <div style='font-size: 0.8em; color: #666;'>
                                Protein: <span style='color: {protein_color}'>{total_protein}g</span><br>
                                Fat: <span style='color: {fat_color}'>{total_fat}g</span><br>
                                Carbs: <span style='color: {carbs_color}'>{total_carbs}g</span>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                
                # Delete day button
                if cols[2].button("🗑️", key=f"delete_day_{day['id']}", help=""):
                    with st.spinner("Deleting day..."):
                        db.delete_data('meals', {'day_id': day['id']})
                        db.delete_data('days', {'id': day['id']})
                        st.rerun()
        except Exception as e:
            st.error(f"Error fetching meal history: {str(e)}")

# =======================
# INSIGHTS TAB (tab3)
# =======================
    with tab3:
        st.title("Insights")

        # --------------------------------------------
        # 1. Initialize DB and fetch user targets
        # --------------------------------------------
        db = initialize_supabase_client()
        if not db:
            st.error("Please log in again.")
            st.stop()

        user_targets = get_user_targets(db)
        if not user_targets:
            st.info("No daily targets set. Please set your targets in the 'View History' tab to see insights.")
            st.stop()

        # --------------------------------------------
        # 2. Fetch days and create a daily DataFrame
        # --------------------------------------------
        days_response = db.select_data(
            'days',
            '*',
            match_dict={'user_id': st.session_state.user.id},
            order_by={'column': 'date', 'ascending': True}  # chronological order
        )

        if not days_response:
            st.info("No meal data found. Log some meals first to see insights!")
            st.stop()

        # Collect daily totals for each day
        daily_rows = []
        for day in days_response:
            # Fetch all meals for that day
            meals_response = db.select_data(
                'meals',
                '*',
                match_dict={'day_id': day['id'], 'user_id': st.session_state.user.id}
            )
            total_calories = sum(m.get('calories', 0) or 0 for m in meals_response)
            total_protein = sum(m.get('protein', 0) or 0 for m in meals_response)
            total_fat = sum(m.get('fat', 0) or 0 for m in meals_response)
            total_carbs = sum(m.get('carbohydrates', 0) or 0 for m in meals_response)

            daily_rows.append({
                "date": pd.to_datetime(day["date"]),
                "calories": total_calories,
                "protein": total_protein,
                "fat": total_fat,
                "carbs": total_carbs
            })

        daily_df = pd.DataFrame(daily_rows).sort_values(by="date")

        # --------------------------------------------
        # 3. Daily Macro Graph
        # --------------------------------------------
        st.subheader("Daily Macro Trends")

        # Dropdown (or radio buttons) to pick which metric to visualize
        metric_option = st.selectbox(
            label="Select a metric to visualize daily trends:",
            options=["calories", "protein", "fat", "carbs"],
            index=0
        )

        # Build a line chart with Altair for the selected metric
        # We'll also overlay a target line if relevant (for "calories" or "protein/fat/carbs"?)
        base_chart = alt.Chart(daily_df).mark_line(point=True).encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y(metric_option + ":Q", title=metric_option.capitalize()),
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip(metric_option + ":Q", title=metric_option.capitalize())
            ]
        ).properties(
            width=700,
            height=350
        )

        # If the user selected "calories", we can overlay the daily calorie target
        # If macros, we can do the daily macro target. Let's do it generally:
        daily_goal = user_targets[metric_option]  # e.g. user_targets["calories"] or user_targets["protein"]
        rule = alt.Chart(pd.DataFrame({"y": [daily_goal]})).mark_rule(
            strokeDash=[4, 4],
            color="red"
        ).encode(y="y:Q")

        daily_chart = alt.layer(base_chart, rule).interactive()
        st.altair_chart(daily_chart, use_container_width=True)

        st.write(f"*Dashed line indicates your daily target for **{metric_option.capitalize()}** = {daily_goal}.*")

        # --------------------------------------------
        # 4. Weekly Insights
        # --------------------------------------------
        st.subheader("Weekly Insights")

        # We'll group daily_df by calendar week and sum up each metric
        # Use ISO calendar (year-week) for grouping
        daily_df["year_week"] = daily_df["date"].dt.isocalendar().year.astype(str) + "-W" + \
                                daily_df["date"].dt.isocalendar().week.astype(str)
        weekly_df = (
            daily_df.groupby("year_week", as_index=False)
            .agg({
                "calories": "sum",
                "protein": "sum",
                "fat": "sum",
                "carbs": "sum"
            })
            .sort_values("year_week")
        )

        # Compute weekly goals (7 × daily targets)
        weekly_goals = {
            "calories": 7 * user_targets["calories"],
            "protein": 7 * user_targets["protein"],
            "fat": 7 * user_targets["fat"],
            "carbs": 7 * user_targets["carbs"]
        }

        # Create a new DataFrame with over/under columns and "status"
        week_insights = []
        for _, row in weekly_df.iterrows():
            w = row["year_week"]
            cal_sum = row["calories"]
            protein_sum = row["protein"]
            fat_sum = row["fat"]
            carbs_sum = row["carbs"]

            # Differences from weekly goals
            cal_diff = cal_sum - weekly_goals["calories"]
            protein_diff = protein_sum - weekly_goals["protein"]
            fat_diff = fat_sum - weekly_goals["fat"]
            carbs_diff = carbs_sum - weekly_goals["carbs"]

            # For a simple "success" measure, let's say:
            #   - If calories are within ±10% of goal, and
            #   - If protein, fat, carbs are each within ±20% of their respective goals
            #   => "Successful week"
            cals_ok = abs(cal_diff) <= 0.10 * weekly_goals["calories"]
            prot_ok = abs(protein_diff) <= 0.20 * weekly_goals["protein"]
            fat_ok  = abs(fat_diff) <= 0.20 * weekly_goals["fat"]
            carbs_ok= abs(carbs_diff) <= 0.20 * weekly_goals["carbs"]

            # A simple approach: all true => success, otherwise not
            success = cals_ok and prot_ok and fat_ok and carbs_ok
            status = "Success" if success else "Not in Range"

            week_insights.append({
                "Week": w,
                "Total Calories": cal_sum,
                "Cal Over/Under": cal_diff,
                "Total Protein": protein_sum,
                "Protein Over/Under": protein_diff,
                "Total Fat": fat_sum,
                "Fat Over/Under": fat_diff,
                "Total Carbs": carbs_sum,
                "Carbs Over/Under": carbs_diff,
                "Status": status
            })

        insights_df = pd.DataFrame(week_insights)

        # Show a cleaner table in Streamlit
        st.dataframe(insights_df)

        st.caption("""
        - **Cal Over/Under** = Total weekly calories minus your weekly calorie goal (7 × daily cals).
        - **Protein/Fat/Carbs Over/Under** = (Same idea for weekly macros).
        - **Status** is a simple check:  
        \t • "Success" if all metrics are within certain percentage thresholds  
        \t • "Not in Range" otherwise.
        """)

        # (Optional) If you want a bar chart for each week's total cals vs. goal, protein vs. goal, etc., 
        # you can add small altair charts here as well.


if __name__ == "__main__":
    main()
