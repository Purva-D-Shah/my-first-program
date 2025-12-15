import streamlit as st
import pandas as pd
import io
import re

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Meesho P&L Dashboard", layout="wide")
st.title("📊 Meesho Profit & Loss Dashboard")
st.markdown("Calculates P&L using **'Reason for Credit Entry'** with robust status matching.")

# --- PAYMENT CHECK (KILL SWITCH) ---
deadline = datetime(2025, 12, 20)
if datetime.now() > deadline:
    st.error("⚠️ License Expired.")
    st.stop()

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    st.header("⚙️ Settings")
    
    st.subheader("1. Cost Sheet")
    uploaded_cost_sheet = st.file_uploader("Upload Cost Sheet (CSV/Excel)", type=['csv', 'xlsx'])
    
    st.write("---")
    st.subheader("2. Fallback Values")
    fallback_product_cost = st.number_input("Avg. Product Cost (₹)", value=0.0, step=10.0)
    packaging_cost = st.number_input("Packaging Cost (₹)", value=5.0, step=1.0)

# --- HELPER FUNCTIONS ---
def clean_currency(x):
    try:
        if pd.isna(x): return 0.0
        return float(str(x).replace(',', '').strip())
    except: return 0.0

def normalize_status(status):
    """Removes spaces, underscores, dashes to make matching robust"""
    if not isinstance(status, str): return "UNKNOWN"
    # Remove non-alphanumeric characters (keep only A-Z and 0-9)
    return re.sub(r'[^a-zA-Z0-9]', '', status).upper()

# --- MAIN APP ---
with st.expander("📂 Upload Files", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        uploaded_order = st.file_uploader("1. Upload Order Report", type=['csv', 'xlsx'])
    with col2:
        uploaded_payments = st.file_uploader("2. Upload Payment Reports", type=['xlsx'], accept_multiple_files=True)

if st.button("Calculate Profit & Loss"):
    if not uploaded_order or not uploaded_payments:
        st.error("⚠️ Please upload both Order and Payment files.")
    else:
        with st.spinner('Analyzing...'):
            try:
                # ==========================================
                # 1. PROCESS COST SHEET
                # ==========================================
                cost_map = {}
                if uploaded_cost_sheet:
                    try:
                        if uploaded_cost_sheet.name.endswith('.csv'): c_df = pd.read_csv(uploaded_cost_sheet)
                        else: c_df = pd.read_excel(uploaded_cost_sheet)
                        
                        # Normalize header
                        c_df.columns = c_df.columns.astype(str).str.lower().str.replace(" ", "")
                        sku_col = next((c for c in c_df.columns if 'sku' in c or 'style' in c), None)
                        cost_col = next((c for c in c_df.columns if 'cost' in c or 'price' in c), None)
                        
                        if sku_col and cost_col:
                            c_df[sku_col] = c_df[sku_col].astype(str).str.strip().str.lower()
                            c_df[cost_col] = c_df[cost_col].apply(clean_currency)
                            cost_map = pd.Series(c_df[cost_col].values, index=c_df[sku_col]).to_dict()
                            st.sidebar.success(f"✅ Loaded {len(cost_map)} SKUs.")
                    except: st.sidebar.warning("Could not process Cost Sheet.")

                # ==========================================
                # 2. PROCESS ORDER REPORT
                # ==========================================
                if uploaded_order.name.endswith('.csv'):
                    try: orders_df = pd.read_csv(uploaded_order)
                    except: 
                        uploaded_order.seek(0)
                        orders_df = pd.read_csv(uploaded_order, encoding='latin1')
                else: orders_df = pd.read_excel(uploaded_order)
                
                # --- COLUMN MAPPING ---
                order_id_col = None
                order_status_col = None
                order_sku_col = None
                
                # Debug Info: Check Columns found
                clean_cols = {c: c.lower().replace(" ", "").replace("_", "").replace('"', '') for c in orders_df.columns}
                
                for original, clean in clean_cols.items():
                    if "suborder" in clean: order_id_col = original
                    if "reasonforcredit" in clean or "orderstatus" in clean: order_status_col = original
                    if "sku" in clean or "style" in clean: order_sku_col = original

                if not order_id_col:
                    st.error(f"❌ 'Sub Order No' not found. Columns found: {list(orders_df.columns)}")
                    st.stop()
                if not order_status_col:
                    st.error(f"❌ 'Reason for Credit Entry' or 'Status' not found. Columns found: {list(orders_df.columns)}")
                    st.stop()

                # Clean IDs
                orders_df[order_id_col] = orders_df[order_id_col].astype(str).str.strip()
                orders_df = orders_df.drop_duplicates(subset=[order_id_col])
                
                # ==========================================
                # 3. PROCESS PAYMENTS
                # ==========================================
                payment_data = []
                total_ads_cost = 0.0
                
                for pay_file in uploaded_payments:
                    try:
                        xls = pd.ExcelFile(pay_file)
                        for sheet in xls.sheet_names:
                            df = pd.read_excel(xls, sheet_name=sheet)
                            if len(df) < 2: continue 
                            
                            # Header Fix
                            temp_cols = [str(c).lower().replace(" ", "") for c in df.columns]
                            if "suborder" not in str(temp_cols) and len(df) > 2:
                                try: df = pd.read_excel(xls, sheet_name=sheet, header=1)
                                except: pass
                            
                            # Normalize Headers
                            df.columns = df.columns.astype(str).str.lower().str.replace(" ", "").str.replace("_", "")
                            
                            p_id = next((c for c in df.columns if "suborder" in c), None)
                            p_amt = next((c for c in df.columns if "finalsettlement" in c or "settlementamount" in c), None)
                            
                            if p_id and p_amt:
                                df[p_id] = df[p_id].astype(str).str.strip()
                                df[p_amt] = df[p_amt].apply(clean_currency)
                                payment_data.append(df[[p_id, p_amt]].rename(columns={p_id: 'suborderno', p_amt: 'amount'}))
                            
                            ads_col = next((c for c in df.columns if "totaladscost" in c or "adcost" in c), None)
                            if ads_col:
                                total_ads_cost += df[ads_col].apply(clean_currency).abs().sum()
                    except: pass
                
                if payment_data:
                    pay_df = pd.concat(payment_data)
                    pay_summary = pay_df.groupby('suborderno')['amount'].sum().reset_index()
                else:
                    pay_summary = pd.DataFrame(columns=['suborderno', 'amount'])

                # ==========================================
                # 4. MERGE & CALCULATE
                # ==========================================
                final_df = pd.merge(orders_df, pay_summary, left_on=order_id_col, right_on='suborderno', how='left')
                final_df['amount'] = final_df['amount'].fillna(0)
                
                # Clean Status for Logic (Remove spaces/underscores)
                # We keep 'Raw Status' for display, create 'Clean Status' for logic
                final_df['Raw Status'] = final_df[order_status_col].fillna("UNKNOWN").astype(str)
                final_df['Clean Status'] = final_df['Raw Status'].apply(normalize_status)

                # --- COST LOGIC ---
                def get_costs(row):
                    status_clean = row['Clean Status']
                    
                    # Fuzzy Match Logic
                    # DELIVERED, DOORSTEP EXCHANGE, LOST
                    apply_prod_cost = status_clean in ['DELIVERED', 'DOORSTEPEXCHANGE', 'LOST']
                    
                    # Packaging: Apply to ALL except CANCELLED
                    apply_pkg_cost = status_clean != 'CANCELLED'
                    
                    # Unit Cost
                    u_cost = fallback_product_cost
                    if order_sku_col:
                        sku = str(row.get(order_sku_col, '')).strip().lower()
                        u_cost = cost_map.get(sku, fallback_product_cost)
                    
                    # Qty
                    qty_val = row.get('Quantity') if 'Quantity' in row else row.get('quantity')
                    qty = pd.to_numeric(qty_val, errors='coerce')
                    if pd.isna(qty): qty = 1
                    
                    prod_cost = (u_cost * qty) if apply_prod_cost else 0.0
                    pkg_cost = packaging_cost if apply_pkg_cost else 0.0
                    
                    return pd.Series([prod_cost, pkg_cost])

                final_df[['Product Cost', 'Packaging Cost']] = final_df.apply(get_costs, axis=1)
                final_df['Net Profit'] = final_df['amount'] - final_df['Product Cost'] - final_df['Packaging Cost']

                # ==========================================
                # 5. DASHBOARD
                # ==========================================
                
                total_settlement = final_df['amount'].sum()
                total_prod = final_df['Product Cost'].sum()
                total_pkg = final_df['Packaging Cost'].sum()
                net_profit = total_settlement - total_prod - total_pkg - total_ads_cost

                st.markdown(f"### 🗓️ Analysis for {len(final_df)} Orders")
                
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Total Settlement", f"₹ {total_settlement:,.0f}")
                k2.metric("Product Costs", f"₹ {total_prod:,.0f}")
                k3.metric("Ads Deducted", f"₹ {total_ads_cost:,.0f}")
                k4.metric("NET PROFIT", f"₹ {net_profit:,.0f}", delta_color="normal")
                
                st.markdown("---")
                st.subheader("📦 Order Status Counts")
                
                # Dynamic Status Counts (Counts every unique status found)
                status_counts = final_df['Raw Status'].value_counts()
                
                # Create dynamic grid
                cols = st.columns(4)
                for i, (status, count) in enumerate(status_counts.items()):
                    with cols[i % 4]:
                        st.metric(label=status, value=count)

                # Download
                st.markdown("---")
                output_cols = [order_id_col, 'Raw Status', 'amount', 'Product Cost', 'Packaging Cost', 'Net Profit']
                if order_sku_col: output_cols.insert(2, order_sku_col)
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    final_df[output_cols].to_excel(writer, index=False)
                st.download_button("📥 Download Detailed Report", buffer, "Profit_Loss_Report.xlsx")

            except Exception as e:
                st.error(f"Error: {e}")
