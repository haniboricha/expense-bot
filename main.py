import streamlit as st
import json
import gspread
import pandas as pd
import io
import sqlite3
import bcrypt
from datetime import datetime
from google import genai
from google.oauth2.service_account import Credentials
from google.genai import types

# --- REPORTLAB IMPORTS FOR IN-MEMORY PDF GENERATION ---
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter

# --- STREAMLIT PAGE CONFIGURATION ---
st.set_page_config(
    page_title="AI Multi-User SaaS Expense Dashboard", 
    page_icon="💰", 
    layout="wide"  
)

# --- CUSTOM CSS FOR PERFECT SPACING AND HEADER VISIBILITY ---
st.markdown("""
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    h1 { margin-bottom: 1rem; font-weight: 700; }
    h2 { margin-top: 1.5rem; margin-bottom: 1rem; }
    .stAlert { margin-top: 1rem; }
    </style>
""", unsafe_allow_html=True)

# =========================================================================
# 🗄️ SQLITE BACKEND DATABASE INITIALIZER
# =========================================================================
DB_FILE = "users.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def create_user(username, password):
    username = username.strip().lower()
    if not username or not password:
        return False, "Username/Password fields cannot be blank."
    
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
        conn.commit()
        conn.close()
        return True, "Account registered successfully!"
    except sqlite3.IntegrityError:
        return False, "Username already exists. Please choose another username."

def verify_user(username, password):
    username = username.strip().lower()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row and bcrypt.checkpw(password.encode('utf-8'), row[0].encode('utf-8')):
        return True
    return False

init_db()

# =========================================================================
# ⚙️ AUTOMATED IN-MEMORY PDF GENERATION WORKER
# =========================================================================
def generate_pdf_report(dataframe, scope_title, total_sum):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=24, leading=28, textColor=colors.HexColor("#1E3A8A"), spaceAfter=6)
    subtitle_style = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontSize=11, leading=14, textColor=colors.HexColor("#4B5563"), spaceAfter=15)
    header_cell_style = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', textColor=colors.white)
    body_cell_style = ParagraphStyle('BodyCell', parent=styles['Normal'], fontSize=9, fontName='Helvetica', textColor=colors.HexColor("#1F2937"))
    
    story.append(Paragraph("AI EXPENSE MANAGEMENT SYSTEM", title_style))
    story.append(Paragraph(f"<b>Statement Scope:</b> {scope_title} | <b>Generated On:</b> {datetime.today().strftime('%Y-%m-%d')}", subtitle_style))
    story.append(Spacer(1, 10))
    
    table_content = [[
        Paragraph("Date", header_cell_style),
        Paragraph("Vendor / Merchant", header_cell_style),
        Paragraph("Category Group", header_cell_style),
        Paragraph("Amount Billed", header_cell_style)
    ]]
    
    for _, row in dataframe.iterrows():
        table_content.append([
            Paragraph(str(row['Date']), body_cell_style),
            Paragraph(str(row['Vendor']), body_cell_style),
            Paragraph(str(row['Category']), body_cell_style),
            Paragraph(f"INR {float(row['Amount']):,.2f}", body_cell_style)
        ])
        
    total_cell_style = ParagraphStyle('TotalCell', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', textColor=colors.HexColor("#1E3A8A"))
    table_content.append([
        Paragraph("<b>NET COMPLIED SUMMARY TOTAL:</b>", total_cell_style),
        Paragraph("", body_cell_style),
        Paragraph("", body_cell_style),
        Paragraph(f"<b>INR {total_sum:,.2f}</b>", total_cell_style)
    ])
    
    statement_table = Table(table_content, colWidths=[80, 180, 140, 110])
    statement_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E3A8A")), 
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.HexColor("#F9FAFB"), colors.white]), 
        ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor("#1E3A8A")),
        ('LINEABOVE', (0,-1), (-1,-1), 1.5, colors.HexColor("#1E3A8A")), 
        ('BOTTOMPADDING', (0,-1), (-1,-1), 8),
        ('TOPPADDING', (0,-1), (-1,-1), 8),
    ]))
    
    story.append(statement_table)
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- INITIALIZATION & CONFIGURATION ---
SPREADSHEET_ID = "1_5_eINIDbQ9A-t5HV14EPerCnEFXYXGrVjp2DJQFnwg"
# 🔴 REMINDER: Insert your secure Gemini API key right here
GEMINI_API_KEY = "AIzaSyDWDpjaaj28ZKoNS2OGfO8Z2JJZCdUjyWA"

@st.cache_resource
def init_connections():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    # 🔍 Check if running on Streamlit Cloud using Secrets
    if "gcp_service_account" in st.secrets:
        # Read credentials directly from the Streamlit Secrets vault
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # ✨ THE CRUCIAL FIX: Convert literal "\n" text into actual hidden newlines
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # Fallback for your local machine testing
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    return sheet, ai_client

try:
    sheet, ai_client = init_connections()
except Exception as e:
    st.error(f"❌ Failed to connect to APIs: {e}")
    st.stop()

# =========================================================================
# 👤 USER ACCOUNT ACCESS INTERFACE
# =========================================================================
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    st.session_state['username'] = ""

if 'auth_view' not in st.session_state:
    st.session_state['auth_view'] = "login"
if 'prefilled_username' not in st.session_state:
    st.session_state['prefilled_username'] = ""

if not st.session_state['authenticated']:
    st.title("🔐 AI Expense SaaS Portal")
    
    if st.session_state['auth_view'] == "login":
        st.subheader("🔒 Member Sign-In")
        
        if 'reg_success_msg' in st.session_state:
            st.success(st.session_state['reg_success_msg'])
            del st.session_state['reg_success_msg']
            
        with st.form("login_form"):
            li_username = st.text_input("Username", value=st.session_state['prefilled_username']).strip().lower()
            li_password = st.text_input("Password", type="password")
            btn_login = st.form_submit_button("Log In to Workspace", use_container_width=True)
            
            if btn_login:
                if verify_user(li_username, li_password):
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = li_username
                    st.session_state['prefilled_username'] = ""
                    st.markdown('<style> .stApp { padding-top: 0px; } </style>', unsafe_allow_html=True)
                    st.rerun()
                else:
                    st.error("Invalid username or password. Try again!")
        
        if st.button("Don't have an account? Register here"):
            st.session_state['auth_view'] = "signup"
            st.rerun()
                    
    else:
        st.subheader("📝 Register New SaaS Profile")
        st.write("Create a secure account to keep your expense logs private.")
        
        with st.form("signup_form"):
            su_username = st.text_input("Choose Unique Username").strip().lower()
            su_password = st.text_input("Choose Secure Password", type="password")
            btn_signup = st.form_submit_button("Register Free Profile Account", use_container_width=True)
            
            if btn_signup:
                success, output_msg = create_user(su_username, su_password)
                if success:
                    st.session_state['prefilled_username'] = su_username
                    st.session_state['reg_success_msg'] = f"🎉 Account '{su_username}' created successfully! Please log in below."
                    st.session_state['auth_view'] = "login" 
                    st.rerun()
                else:
                    st.error(output_msg)
                    
        if st.button("Already have an account? Log in here"):
            st.session_state['auth_view'] = "login"
            st.rerun()
                    
    st.stop() 

# --- SIDEBAR ACCOUNT OPTIONS ---
st.sidebar.markdown(f"### 👤 Logged in as: **{st.session_state['username']}**")
if st.sidebar.button("🚪 Log Out", use_container_width=True):
    st.session_state['authenticated'] = False
    st.session_state['username'] = ""
    st.session_state['auth_view'] = "login"
    st.rerun()

# --- PERSISTENT SUCCESS NOTIFICATION ---
if 'success_msg' in st.session_state:
    st.success(st.session_state['success_msg'])
    if st.session_state.get('show_balloons'):
        st.balloons()
    del st.session_state['success_msg']
    if 'show_balloons' in st.session_state:
        del st.session_state['show_balloons']

# =========================================================================
# 📤 CONTAINER FOR UPLOADING RECEIPTS
# =========================================================================
st.subheader("📥 Add New Receipt / Log Expense")
with st.container(border=True):
    up_col1, up_col2, up_col3 = st.columns([1, 2, 1])
    
    with up_col2:
        if 'uploader_key' not in st.session_state:
            st.session_state['uploader_key'] = 0

        uploaded_file = st.file_uploader(
            "📸 Drag and drop or browse a receipt image to auto-process with AI", 
            type=["jpg", "jpeg", "png"],
            key=f"receipt_uploader_{st.session_state['uploader_key']}"
        )

    if uploaded_file is not None:
        img_col, form_col = st.columns([1, 1], gap="medium")
        
        with img_col:
            st.image(uploaded_file, caption="Uploaded Receipt Preview", use_container_width=True)
            if st.button("✨ Analyze Receipt With AI", use_container_width=True):
                with st.spinner("🧠 Gemini 3 Flash parsing layout..."):
                    try:
                        image_bytes = uploaded_file.read()
                        mime_type = uploaded_file.type

                        response = ai_client.models.generate_content(
                            model="gemini-3-flash-preview",
                            contents=[
                                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                                "Extract receipt data to JSON: {date, vendor, amount, category}. Use YYYY-MM-DD for date. "
                                "Categorize into standard buckets like Food & Drink, Transport, Shopping, Utilities, or Misc. "
                                "Return only raw JSON code string without markdown block characters."
                            ]
                        )

                        raw_text = response.text.strip()
                        # 🔧 FIXED: Indentation sanitation string parser block
                        if "```" in raw_text:
                            raw_text = raw_text.split("```")[1]
                            if raw_text.startswith("json"):
                                raw_text = raw_text[4:]
                        
                        parsed_data = json.loads(raw_text.strip())
                        st.session_state['active_receipt'] = parsed_data
                        st.success("🎉 Processed successfully!")
                    except Exception as e:
                        st.error(f"❌ Error during processing: {e}")

        with form_col:
            if 'active_receipt' in st.session_state:
                st.subheader("📝 Verify Details")
                receipt_data = st.session_state['active_receipt']
                
                with st.form("verification_form"):
                    form_date = st.text_input("Date (YYYY-MM-DD)", value=receipt_data.get('date', datetime.today().strftime('%Y-%m-%d')))
                    form_vendor = st.text_input("Vendor / Merchant", value=receipt_data.get('vendor', 'Unknown'))
                    
                    try:
                        raw_amount_str = str(receipt_data.get('amount', '0.0')).strip()
                        for char in ['$', '₹', ',', ' ']:
                            raw_amount_str = raw_amount_str.replace(char, '')
                        initial_amount = float(raw_amount_str)
                    except ValueError:
                        initial_amount = 0.0
                        
                    form_amount = st.number_input("Amount", value=initial_amount, step=0.01)
                    
                    categories = ["Food & Drink", "Transport", "Shopping", "Software/Subscriptions", "Utilities", "Misc"]
                    ai_suggested_cat = receipt_data.get('category', 'Misc')
                    default_index = categories.index(ai_suggested_cat) if ai_suggested_cat in categories else 5
                    form_category = st.selectbox("Category Allocation", categories, index=default_index)
                    
                    submit_btn = st.form_submit_button("🚀 Commit & Log to Google Sheet", use_container_width=True)
                    
                    if submit_btn:
                        try:
                            current_user = st.session_state['username']
                            sheet.append_row([current_user, form_date, form_vendor, form_amount, form_category])
                            st.session_state['success_msg'] = f"✅ Logged successfully: {form_vendor} — ₹{form_amount}"
                            st.session_state['show_balloons'] = True
                            del st.session_state['active_receipt']
                            st.session_state['uploader_key'] += 1
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as sheet_error:
                            st.error(f"❌ Failed to commit row: {sheet_error}")

st.markdown("---")

# =========================================================================
# 📊 SECTION 2: BOTTOM PANEL - DASHBOARD MATRIX OVERVIEW
# =========================================================================
st.header("📊 Expense Analytics Dashboard Overview")

try:
    raw_rows = sheet.get_all_values()
    
    if len(raw_rows) <= 1:
        st.info("📉 No expense logs found in your Google Sheet yet. Start logging to populate the metrics!")
    else:
        headers = [h.strip() for h in raw_rows[0]]
        df = pd.DataFrame(raw_rows[1:], columns=headers)
        
        current_logged_user = st.session_state['username']
        df = df[df['User_ID'].str.strip().str.lower() == current_logged_user]
        
        if df.empty:
            st.info(f"👋 Welcome to the platform, **{current_logged_user}**! You haven't added any receipts yet. Use the upload field above to capture your first expense log.")
        else:
            df['Amount'] = df['Amount'].astype(str).str.replace('₹','').str.replace('$','').str.replace(',','').str.strip()
            df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0.0)
            df['Date'] = df['Date'].fillna('').str.strip()
            
            def parse_flexible_date(date_str):
                if not date_str:
                    return None
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y", "%d-%m-%Y"):
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
                try:
                    return pd.to_datetime(date_str, dayfirst=True, errors='coerce')
                except:
                    return None

            df['ParsedDate'] = df['Date'].apply(parse_flexible_date)
            df['ParsedDate'] = df['ParsedDate'].fillna(datetime.today())
            
            df['Year'] = df['ParsedDate'].dt.year.astype(str)
            df['Month'] = df['ParsedDate'].dt.strftime('%B')
            df = df.sort_values(by='ParsedDate', ascending=False)
            
            f_col1, f_col2, f_col3 = st.columns(3)
            
            with f_col1:
                year_options = ["All Years"] + sorted(list(df['Year'].unique()), reverse=True)
                selected_year = st.selectbox("Select Filter Year", year_options)
                
            with f_col2:
                if selected_year != "All Years":
                    available_months = list(df[df['Year'] == selected_year]['Month'].unique())
                else:
                    available_months = list(df['Month'].unique())
                month_options = ["All Months"] + sorted(available_months)
                selected_month = st.selectbox("Select Filter Month", month_options)
                
            with f_col3:
                category_options = ["All Categories"] + sorted(list(df['Category'].unique()))
                selected_category = st.selectbox("Select Filter Category", category_options)
            
            filtered_df = df.copy()
            if selected_year != "All Years":
                filtered_df = filtered_df[filtered_df['Year'] == selected_year]
            if selected_month != "All Months":
                filtered_df = filtered_df[filtered_df['Month'] == selected_month]
            if selected_category != "All Categories":
                filtered_df = filtered_df[filtered_df['Category'] == selected_category]
                
            total_spent = filtered_df['Amount'].sum()
            
            # --- SMART MONTHLY BUDGET ALERTS CONTAINER ---
            st.markdown("---")
            st.subheader("🚨 Monthly Budget Guardrails")
            b_col1, b_col2 = st.columns([1.5, 2], gap="large")
            
            with b_col1:
                monthly_budget_cap = st.number_input(
                    "🎯 Set Monthly Spending Target (₹)", 
                    min_value=1000, max_value=50000, value=10000, step=1000
                )
                
            with b_col2:
                burn_rate_pct = (total_spent / monthly_budget_cap)
                clamped_pct = min(float(burn_rate_pct), 1.0)
                
                st.write(f"**Budget Utilization Status:** Used **₹{total_spent:,.2f}** out of your **₹{monthly_budget_cap:,.2f}** limit threshold.")
                
                if burn_rate_pct >= 1.0:
                    st.error(f"❌ **ALERT:** You have overspent your monthly target limit by **₹{total_spent - monthly_budget_cap:,.2f}**!")
                    st.progress(clamped_pct)
                elif burn_rate_pct >= 0.70:
                    st.warning(f"⚠️ **WARNING:** You have burned through **{burn_rate_pct*100:.1f}%** of your wallet scope!")
                    st.progress(clamped_pct)
                else:
                    st.success(f"✅ **ALL CLEAR:** Wallet metrics are perfectly sound. You have **{100 - (burn_rate_pct*100):.1f}%** remaining.")
                    st.progress(clamped_pct)
                    
            st.markdown("---")
            
            # --- HIGHLIGHT METRIC CARDS ---
            st.markdown("### 🔍 Summary Overview")
            m1, m2, m3 = st.columns([1, 1, 1.2]) 
            with m1:
                st.metric(label=f"📉 Selected Framework Net Spending Total", value=f"₹{total_spent:,.2f}")
            with m2:
                st.metric(label="🔢 Matching Records Count", value=len(filtered_df))
                
            with m3:
                st.write("📂 **Corporate Reporting**")
                scope_string = f"{selected_month} {selected_year if selected_year != 'All Years' else 'All-Time'}"
                if selected_category != "All Categories":
                    scope_string += f" ({selected_category})"
                    
                pdf_data_df = filtered_df.copy()
                pdf_data_df['Date'] = pdf_data_df['ParsedDate'].dt.strftime('%Y-%m-%d')
                pdf_data_df = pdf_data_df[['Date', 'Vendor', 'Category', 'Amount']]
                
                pdf_bytes_payload = generate_pdf_report(pdf_data_df, scope_string, total_spent)
                
                st.download_button(
                    label="📥 Download Filtered PDF Statement",
                    data=pdf_bytes_payload,
                    file_name=f"Expense_Report_{selected_month}_{selected_year}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
            st.markdown("---")
            
            # --- DATA LAYOUT PANEL SPLIT ---
            graph_pane, statement_pane = st.columns([1, 1], gap="large")
            
            with graph_pane:
                st.subheader("📊 Category Spending Allocation")
                chart_data = filtered_df.groupby('Category')['Amount'].sum().reset_index()
                if not chart_data.empty and total_spent > 0:
                    chart_data = chart_data.set_index('Category')
                    st.bar_chart(chart_data, y="Amount", use_container_width=True)
                else:
                    st.info("No categorical data to render visualization charts for this criteria selection.")
                    
            with statement_pane:
                st.subheader("📋 Filtered Transaction Ledger")
                display_df = filtered_df.copy()
                display_df['DateDisplay'] = display_df['ParsedDate'].dt.strftime('%Y-%m-%d')
                display_df = display_df[['DateDisplay', 'Vendor', 'Amount', 'Category']].rename(columns={'DateDisplay': 'Date'})
                st.dataframe(display_df, use_container_width=True, hide_index=True)
            
except Exception as data_error:
    st.error(f"❌ Dashboard Presentation Error: {data_error}")