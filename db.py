from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import os

class SupabaseRLS:
    """
    A single class that handles both:
      - User authentication (sign in/out)
      - Database queries with row-level security (RLS) enforced by Supabase.
      
    NOTE: This code is compatible with the newer supabase-py library (>= 1.0).
    """

    def __init__(self, supabase_url: str, supabase_anon_key: str):
        """
        :param supabase_url: Your Supabase project URL
        :param supabase_anon_key: Your Supabase anon/public key
        """
        self.supabase_url = supabase_url
        self.supabase_anon_key = supabase_anon_key
        # Create the initial client (not signed in yet).
        self.client: Client = create_client(supabase_url, supabase_anon_key)
        # Will hold user session after login.
        self.session: Optional[Dict[str, Any]] = None

    def sign_in(self, email: str, password: str) -> Dict[str, Any]:
        """
        Sign in an existing user with email and password.
        """
        try:
            response = self.client.auth.sign_in_with_password({"email": email, "password": password})
            self.session = response.session
            return {
                "user": response.user,
                "session": response.session,
                "expires_in": response.session.expires_in if response.session else None
            }
        except Exception as e:
            raise Exception(f"Sign in failed: {str(e)}")

    def sign_out(self) -> None:
        """
        Sign out the user. This clears the session from the client.
        """
        self.client.auth.sign_out()
        self.session = None

    def get_current_session(self) -> Optional[Dict[str, Any]]:
        """
        Returns the currently stored session, or None if not signed in.
        """
        return self.session

    def select_data(
        self, 
        table_name: str, 
        columns: str = "*", 
        match_dict: Dict[str, Any] = None,
        order_by: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Select data from the specified table using the currently authenticated user.
        
        Args:
            table_name (str): Name of the table to query
            columns (str): Columns to select
            match_dict (Dict[str, Any]): Dictionary of column/value pairs to filter by
            order_by (Dict[str, Any]): Dictionary with 'column' and 'ascending' keys for ordering
        """
        try:
            query = self.client.table(table_name).select(columns)
            
            # Apply filters if provided
            if match_dict:
                query = query.match(match_dict)
                
            # Apply ordering if provided
            if order_by and 'column' in order_by:
                ascending = order_by.get('ascending', True)
                query = query.order(
                    order_by['column'],
                    desc=(not ascending)
                )
                
            response = query.execute()
            return response.data
        except Exception as e:
            raise Exception(f"Select failed: {str(e)}")

    def insert_data(self, table_name: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Insert data into the specified table, with RLS enforced for the current user.
        Returns the inserted rows.
        """
        try:
            response = self.client.table(table_name).insert(data).execute()
            return response.data  # List of inserted rows
        except Exception as e:
            raise Exception(f"Insert failed: {str(e)}")

    def update_data(self, table_name: str, match_dict: Dict[str, Any], new_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Update rows in the specified table, with RLS enforced for the current user.
        Returns the updated rows.
        """
        try:
            response = (
                self.client
                .table(table_name)
                .update(new_data)
                .match(match_dict)
                .execute()
            )
            return response.data  # List of updated rows
        except Exception as e:
            raise Exception(f"Update failed: {str(e)}")

    def delete_data(self, table_name: str, match_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Delete rows from the specified table, with RLS enforced for the current user.
        Returns the deleted rows.
        """
        try:
            response = (
                self.client
                .table(table_name)
                .delete()
                .match(match_dict)
                .execute()
            )
            return response.data  # List of deleted rows
        except Exception as e:
            raise Exception(f"Delete failed: {str(e)}")

    def sign_up(self, email: str, password: str) -> Dict[str, Any]:
        """
        Sign up a new user with email and password.
        """
        try:
            response = self.client.auth.sign_up({
                "email": email,
                "password": password
            })
            return {
                "user": response.user,
                "session": response.session,
            }
        except Exception as e:
            raise Exception(f"Sign up failed: {str(e)}")

    def request_password_reset(self, email: str) -> bool:
        """
        Send a password reset email to the user.
        Returns True if email was sent successfully.
        """
        try:
            self.client.auth.reset_password_email(email)
            return True
        except Exception as e:
            raise Exception(f"Password reset request failed: {str(e)}")

    def update_password(self, new_password: str, access_token: str = None, refresh_token: str = None) -> bool:
        """
        Update the user's password (used after password reset)
        """
        try:
            if access_token and refresh_token:
                # Set the session first if tokens are provided
                self.client.auth.set_session(access_token, refresh_token)
            self.client.auth.update_user({"password": new_password})
            return True
        except Exception as e:
            raise Exception(f"Password update failed: {str(e)}")

