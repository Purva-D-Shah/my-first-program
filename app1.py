import streamlit as st
import pandas as pd
from datetime import datetime
import io

# --- PAYMENT CHECK ---
# Set the deadline date (Year, Month, Day)
deadline = datetime(2025, 12, 20) 

if datetime.now() > deadline:
    st.error("⚠️ This tool's license has expired. Please contact the developer to renew access.")
    st.stop() # This completely stops the rest of the code from running

st.set_page_config(page_title="Meesho Profit Calculator", layout="centered")
st.title("📊 Meesho Profit & Loss Reconciler")

# --- 1. UPLOAD FILES ---
st.markdown("### Step 1: Upload Files")
uploaded_order = st.file_uploader("Upload Order Report (CSV/Excel)", type=['csv', 'xlsx'])
uploaded_payments = st.file_uploader("Upload Payment Reports (Select Multiple)", type=['xlsx'], accept_multiple_files=True)

def normalize_columns(df):
    """
    Standardizes column names to help find 'Sub Order No'.
    Example: "Sub Order No " -> "suborderno"
    """
    df.columns = df.columns.astype(str).str.lower().str.replace(" ", "").str.replace("_", "").str.replace(".", "")
    return df

if st.button("Calculate Profit"):
    if not uploaded_order or not uploaded_payments:
        st.error("⚠️ Please upload both Order and Payment files.")
    else:
        with st.spinner('Processing...'):
            try:
                # ==========================================
                # 1. PROCESS ORDER FILE
                # ==========================================
                if uploaded_order.name.endswith('.csv'):
                    # Try reading with different encodings if default fails
                    try:
                        orders_df = pd.read_csv(uploaded_order)
                    except:
                        uploaded_order.seek(0)
                        orders_df = pd.read_csv(uploaded_order, encoding='latin1')
                else:
                    orders_df = pd.read_excel(uploaded_order)
                
                # Normalize headers
                normalize_columns(orders_df)
                
                # Find Order ID Column
                order_id_col = None
                for col in orders_df.columns:
                    if "suborder" in col: 
                        order_id_col = col
                        break
                
                if not order_id_col:
                    st.error(f"❌ Could not find 'Sub Order No' in Order File.")
                    st.stop()
                
                # Force ID to String
                orders_df[order_id_col] = orders_df[order_id_col].astype(str)

                # ==========================================
                # 2. PROCESS PAYMENT FILES
                # ==========================================
                payment_frames = []
                debug_logs = []

                for pay_file in uploaded_payments:
                    try:
                        xls = pd.ExcelFile(pay_file)
                    except Exception as e:
                        st.warning(f"Skipping corrupt file {pay_file.name}: {e}")
                        continue

                    file_processed = False
                    
                    # Loop through ALL sheets
                    for sheet in xls.sheet_names:
                        try:
                            # Read the sheet
                            df = pd.read_excel(xls, sheet_name=sheet)
                            
                            # SKIP CHECK: If sheet is too small (Disclaimer/Instruction sheets)
                            if len(df) < 2:
                                debug_logs.append(f"Skipped sheet '{sheet}' - Too few rows (Disclaimer?)")
                                continue

                            # Normalize to check for columns
                            temp_cols = df.columns.astype(str).str.lower().str.replace(" ", "")
                            
                            # CHECK 1: Is header on Row 1?
                            if "suborder" not in str(list(temp_cols)):
                                # CHECK 2: Try Row 2 (Header=1) if row count allows
                                if len(df) > 2:
                                    try:
                                        df = pd.read_excel(xls, sheet_name=sheet, header=1)
                                    except:
                                        pass # Just fallback to previous read
                            
                            # Normalize again after potential reload
                            normalize_columns(df)
                            
                            # Find Columns
                            pay_id_col = None
                            pay_amount_col = None
                            
                            for col in df.columns:
                                if "suborder" in col:
                                    pay_id_col = col
                                if "finalsettlement" in col or "settlementamount" in col:
                                    pay_amount_col = col
                            
                            # If we found both columns, we are good!
                            if pay_id_col and pay_amount_col:
                                df = df.rename(columns={pay_id_col: 'suborderno', pay_amount_col: 'amount'})
                                # Clean data
                                df = df.dropna(subset=['suborderno'])
                                df['suborderno'] = df['suborderno'].astype(str)
                                payment_frames.append(df[['suborderno', 'amount']])
                                file_processed = True
                                break # Stop looking at other sheets in this file (assuming 1 data sheet per file)
                            
                        except Exception as e:
                            debug_logs.append(f"Error reading sheet '{sheet}': {str(e)}")

                    if not file_processed:
                        st.error(f"❌ Could not find valid data in file: {pay_file.name}")
                        st.write("Please check if the column 'Sub Order No' exists.")
                        with st.expander("Debug Log"):
                            st.write(debug_logs)
                        st.stop()

                # ==========================================
                # 3. MERGE & CALCULATE
                # ==========================================
                if payment_frames:
                    all_payments = pd.concat(payment_frames)
                    
                    # Sum payments by ID (Handle duplicates/partials)
                    # Convert amount to numeric, force errors to NaN then 0
                    all_payments['amount'] = pd.to_numeric(all_payments['amount'], errors='coerce').fillna(0)
                    payment_summary = all_payments.groupby("suborderno")["amount"].sum().reset_index()
                    
                    # Merge
                    final_df = pd.merge(orders_df, payment_summary, left_on=order_id_col, right_on="suborderno", how="left")
                    final_df["amount"] = final_df["amount"].fillna(0)
                    
                    # --- PROFIT MATH ---
                    # 1. Product Cost (Placeholder or look for column)
                    if "productcost" in final_df.columns:
                         final_df["final_product_cost"] = final_df["productcost"]
                    else:
                         final_df["final_product_cost"] = 0 # Dummy value
                    
                    # 2. Packaging (Fixed)
                    final_df["final_packaging_cost"] = 5 
                    
                    # 3. Net Profit
                    final_df["Net_Profit"] = final_df["amount"] - final_df["final_product_cost"] - final_df["final_packaging_cost"]

                    # ==========================================
                    # 4. OUTPUT
                    # ==========================================
                    st.success("✅ Reconciliation Complete!")
                    
                    # Metric Cards
                    col1, col2 = st.columns(2)
                    col1.metric("Total Orders", len(final_df))
                    col2.metric("Total Profit Estimate", f"₹ {final_df['Net_Profit'].sum():,.2f}")

                    st.dataframe(final_df[[order_id_col, "amount", "Net_Profit"]].head())
                    
                    # Download Button
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        final_df.to_excel(writer, index=False)
                        
                    st.download_button(
                        label="📥 Download Profit Report",
                        data=buffer,
                        file_name="Meesho_Profit_Loss_Report.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

            except Exception as e:
                st.error(f"Critical Error: {e}")

                st.write("Tip: Check if your CSV file is open in Excel. Close it and try again.")
