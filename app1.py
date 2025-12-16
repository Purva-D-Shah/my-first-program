import streamlit as st
import pandas as pd
import io
import re
from datetime import datetime
import numpy as np

# --- CRITICAL FIX: Set Fallback Cost to 0.0 when field is removed ---
fallback_product_cost = 0.0

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Meesho P&L Reconciliation", layout="wide")
st.title("💰 Meesho P&L Reconciliation Tool (Final)")
st.markdown("Builds the P&L report using the Order Detail Report as the master template.")

# --- HELPER FUNCTIONS ---
def clean_currency(x):
    """Converts string currency (with commas/non-numeric) to float."""
    try:
        if pd.isna(x): return 0.0
        return float(str(x).replace(',', '').strip())
    except: return 0.0

def normalize_status(status):
    """Removes non-alphanumeric chars for robust status matching."""
    if not isinstance(status, str): return "UNKNOWN"
    return re.sub(r'[^a-zA-Z0-9]', '', status).upper()

def find_column(df, keywords):
    """Finds the original column name based on normalized keywords."""
    for col in df.columns:
        c_clean = col.lower().replace(" ", "").replace("_", "").replace(".", "").replace('"', '')
        if any(k in c_clean for k in keywords):
            return col
    return None

def read_excel_safely(file):
    """Attempts to read Excel sheets robustly, handling header and formula rows."""
    try:
        xls = pd.ExcelFile(file)
        data_frames = {}
        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet, header=0)
            
            if len(df) > 1 and 'Order Date' in str(df.iloc[0, 1]): 
                df = pd.read_excel(xls, sheet_name=sheet, header=1)
                df = df.iloc[1:] # Drop the formula row
            
            df.columns = [c.lower().replace(" ", "").replace("_", "") for c in df.columns]
            data_frames[sheet] = df
        return data_frames
    except: return {}

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    st.header("⚙️ Cost Settings")
    
    st.subheader("1. Cost Sheet (SKU Mapping)")
    uploaded_cost_sheet = st.file_uploader("Upload Cost Sheet (CSV/Excel)", type=['csv', 'xlsx'], help="Must have 'SKU' and 'Cost' columns.")
    
    st.write("---")
    st.subheader("2. Fallback Values")
    st.info(f"Fallback Product Cost is set to ₹{fallback_product_cost:.2f} (Hardcoded).")
    packaging_cost = st.number_input("Packaging Cost (₹)", value=5.0, step=1.0, help="Packaging cost per order.")

# --- MAIN APP ---
with st.expander("📂 Upload Files", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        uploaded_order = st.file_uploader("1. Order Detail Report (Master List)", type=['csv', 'xlsx'])
    with col2:
        st.info("Upload payments in chronological order.")
        uploaded_payment_1 = st.file_uploader("2. Payment File 1 (Same Month Pay / Ads Source)", type=['xlsx'])
        uploaded_payment_2 = st.file_uploader("3. Payment File 2 (Next Month Pay)", type=['xlsx'])
        
        uploaded_payments = [f for f in [uploaded_payment_1, uploaded_payment_2] if f is not None]


if st.button("Generate Reconciliation Sheet"):
    if not uploaded_order or len(uploaded_payments) < 1:
        st.error("⚠️ Please upload the Order Detail Report and at least Payment File 1.")
    else:
        with st.spinner('Processing and reconciling data...'):
            try:
                # ==========================================
                # 1. PROCESS COST SHEET
                # ==========================================
                cost_map = {}
                if uploaded_cost_sheet:
                    try:
                        if uploaded_cost_sheet.name.endswith('.csv'): c_df = pd.read_csv(uploaded_cost_sheet)
                        else: c_df = pd.read_excel(uploaded_cost_sheet)
                        c_df.columns = c_df.columns.astype(str).str.lower().str.strip()
                        sku_col = next((c for c in c_df.columns if 'sku' in c or 'style' in c), None)
                        cost_col = next((c for c in c_df.columns if 'cost' in c or 'price' in c), None)
                        if sku_col and cost_col:
                            c_df[sku_col] = c_df[sku_col].astype(str).str.strip().str.lower()
                            c_df[cost_col] = c_df[cost_col].apply(clean_currency)
                            cost_map = pd.Series(c_df[cost_col].values, index=c_df[sku_col]).to_dict()
                    except: pass

                # ==========================================
                # 2. PROCESS ORDER REPORT (MASTER TEMPLATE)
                # ==========================================
                if uploaded_order.name.endswith('.csv'): orders_df = pd.read_csv(uploaded_order, encoding='latin1')
                else: orders_df = pd.read_excel(uploaded_order)
                
                orders_df.columns = orders_df.columns.astype(str).str.strip() 
                
                order_id_col = find_column(orders_df, ["suborder", "orderid"])
                order_sku_col = find_column(orders_df, ["sku", "style"])
                order_qty_col = find_column(orders_df, ["quantity"])
                order_status_col = find_column(orders_df, ["reasonforcredit", "orderstatus"])
                
                if not all([order_id_col, order_sku_col, order_qty_col, order_status_col]):
                    st.error("❌ Critical columns missing in Order Report. Check file structure.")
                    st.stop()

                orders_df[order_id_col] = orders_df[order_id_col].astype(str).str.strip()
                orders_df = orders_df.drop_duplicates(subset=[order_id_col])

                reconciliation_df = orders_df.copy()
                reconciliation_df['Sub Order ID'] = reconciliation_df[order_id_col]
                reconciliation_df['Raw Status'] = reconciliation_df[order_status_col].astype(str).str.strip()
                reconciliation_df['Clean Status'] = reconciliation_df['Raw Status'].apply(normalize_status)
                
                # ==========================================
                # 3. PROCESS PAYMENTS & ADS
                # ==========================================
                payment_summaries = {}
                total_ads_cost = 0.0

                for idx, pay_file in enumerate(uploaded_payments):
                    file_key = f"pay_{idx+1}"
                    df_sheets = read_excel_safely(pay_file)
                    
                    for df in df_sheets.values():
                        if idx == 0: # File 1 is the Ads Source
                            ads_col = next((c for c in df.columns if "totaladscost" in c), None)
                            if ads_col: total_ads_cost += df[ads_col].apply(clean_currency).abs().sum()
                        
                        p_id = next((c for c in df.columns if "suborder" in c), None)
                        p_amt = next((c for c in df.columns if "finalsettlement" in c or "settlementamount" in c), None)
                        
                        if p_id and p_amt:
                            df[p_id] = df[p_id].astype(str).str.strip()
                            df[p_amt] = df[p_amt].apply(clean_currency)
                            summary = df.groupby(p_id)[p_amt].sum().reset_index()
                            payment_summaries[file_key] = summary.rename(columns={p_id: 'Sub Order ID', p_amt: f'Payment_{file_key}'})
                            break
                    if idx == 0: st.info(f"Total Ads Cost (from File 1): ₹ {total_ads_cost:,.2f}")

                # ==========================================
                # 4. FINAL MERGE & CALCULATIONS
                # ==========================================
                
                final_df = pd.merge(reconciliation_df, payment_summaries.get('pay_1', pd.DataFrame({'Sub Order ID':[], 'Payment_pay_1':[]})), on='Sub Order ID', how='left')
                final_df = pd.merge(final_df, payment_summaries.get('pay_2', pd.DataFrame({'Sub Order ID':[], 'Payment_pay_2':[]})), on='Sub Order ID', how='left')
                
                # Fill NaNs for payment columns
                final_df['Same Month Pay'] = final_df.get('Payment_pay_1', 0.0).fillna(0)
                final_df['Next Month Pay'] = final_df.get('Payment_pay_2', 0.0).fillna(0)
                
                # --- User Requested Columns ---
                final_df['Total'] = final_df['Same Month Pay'] + final_df['Next Month Pay']

                # SKU Cost Mapping
                final_df['Cost Price (Unit)'] = final_df[order_sku_col].apply(
                    lambda sku: cost_map.get(str(sku).strip().lower(), fallback_product_cost)
                )

                # Final Cost = Quantity * Cost Price
                def calculate_product_cost(row):
                    status = row['Clean Status']
                    apply_prod_cost = status in ['DELIVERED', 'DOORSTEPEXCHANGE', 'LOST']
                    
                    if apply_prod_cost:
                        qty = row[order_qty_col] if pd.notna(row[order_qty_col]) else 1
                        return row['Cost Price (Unit)'] * qty
                    return 0.0

                final_df['Final Cost'] = final_df.apply(calculate_product_cost, axis=1)

                # Net Cost (Per Order) = Total Payment - Final Cost (Product)
                final_df['Net Cost'] = final_df['Total'] - final_df['Final Cost']

                # Packaging Cost (Dynamic Default)
                final_df['Packaging Cost (Per Order)'] = final_df['Clean Status'].apply(
                    lambda status: packaging_cost if status != 'CANCELLED' else 0.0
                )
                
                # Net Profit (Per Order)
                final_df['Net Profit'] = final_df['Total'] - final_df['Final Cost'] - final_df['Packaging Cost (Per Order)']

                # ==========================================
                # 5. SUMMARY CALCULATIONS & DASHBOARD
                # ==========================================
                
                total_payment = final_df['Total'].sum()
                total_product_cost_sum = final_df['Final Cost'].sum()
                total_packaging_cost_sum = final_df['Packaging Cost (Per Order)'].sum()
                
                final_profit_loss = total_payment - (total_product_cost_sum + total_packaging_cost_sum + total_ads_cost)

                # Dashboard Display
                st.header("1. P&L Summary")
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Payment Received", f"₹ {total_payment:,.0f}")
                c2.metric("Total P&L", f"₹ {final_profit_loss:,.0f}", delta_color="normal")
                c3.metric("Total Product Cost", f"₹ {total_product_cost_sum:,.0f}")
                c4.metric("Total Ads Overhead", f"₹ {total_ads_cost:,.0f}")

                st.markdown("---")
                st.header("2. Detailed Status Counts")
                status_counts = final_df['Raw Status'].value_counts()
                
                cols = st.columns(4)
                for i, (status, count) in enumerate(status_counts.items()):
                    with cols[i % 4]: st.metric(label=status, value=count)
                
                # --- Final Output Report Creation ---
                original_cols = orders_df.columns.tolist()
                pnl_cols_to_append = ['Same Month Pay', 'Next Month Pay', 'Total', 'Cost Price (Unit)', 'Final Cost', 'Net Cost', 'Packaging Cost (Per Order)', 'Net Profit']
                
                df_final_report = final_df[original_cols + pnl_cols_to_append].copy()

                st.markdown("---")
                st.header("3. Final Reconciled Report")
                st.dataframe(df_final_report)

                # --- Download ---
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df_final_report.to_excel(writer, sheet_name='Final Reconciled Report', index=False)
                    
                st.download_button("📥 Download Final Reconciled Report (Excel)", buffer, "Meesho_Recon_Final.xlsx")

            except Exception as e:
                st.error(f"A critical error occurred. Please check your file inputs: {e}")
                st.exception(e)
