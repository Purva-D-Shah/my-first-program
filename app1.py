import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# --- Configuration ---
st.set_page_config(
    page_title="Meesho Profit/Loss Calculator",
    layout="wide",
    initial_sidebar_state="expanded"
)

def read_flexible(file, sheet_name=None, header=1, usecols=None, names=None):
    """Helper to read either CSV or Excel files based on extension."""
    file.seek(0)
    if file.name.endswith('.csv'):
        # For CSVs, we use indices for usecols if names are provided
        return pd.read_csv(file, skiprows=header, usecols=range(max(usecols)+1) if isinstance(usecols, list) else None).iloc[:, usecols]
    else:
        return pd.read_excel(file, sheet_name=sheet_name, header=header, usecols=usecols)

def process_data(orders_file, same_month_file, next_month_file, cost_file, packaging_cost_value, misc_cost_value):
    try:
        # --- A. Read Orders ---
        df_orders = pd.read_csv(orders_file)

        # --- B. Read Order Payments (Including Payment Date for latest status logic) ---
        # Indices: A=0 (Sub Order No), F=5 (Live Order Status), K=10 (Payment Date), L=11 (Final Settlement Amount)
        pay_cols_indices = [0, 5, 10, 11]
        pay_cols_names = ["Sub Order No", "Live Order Status", "Payment Date", "Final Settlement Amount"]

        # Handling both CSV and Excel for payment files
        same_month_file.seek(0)
        if same_month_file.name.endswith('.csv'):
            df_same = pd.read_csv(same_month_file, skiprows=1, usecols=pay_cols_indices, names=pay_cols_names)
        else:
            df_same = pd.read_excel(same_month_file, sheet_name='Order Payments', header=1, usecols="A,F,K,L", names=pay_cols_names)

        next_month_file.seek(0)
        if next_month_file.name.endswith('.csv'):
            df_next = pd.read_csv(next_month_file, skiprows=1, usecols=pay_cols_indices, names=pay_cols_names)
        else:
            df_next = pd.read_excel(next_month_file, sheet_name='Order Payments', header=1, usecols="A,F,K,L", names=pay_cols_names)

        # --- C. Read Cost File ---
        cost_file.seek(0)
        if cost_file.name.endswith('.csv'):
            df_cost = pd.read_csv(cost_file)
        else:
            df_cost = pd.read_excel(cost_file)

        # --- D. Read Ads Cost (Column H = Index 7) ---
        def get_ads_sum(file):
            file.seek(0)
            if file.name.endswith('.csv'):
                # Assuming 'Ads Cost' is a separate file or we need to filter if it's the same file
                # If these are the exported multi-sheet CSVs you uploaded:
                ads_df = pd.read_csv(file, skiprows=1)
                col = 'Total Ads Cost' if 'Total Ads Cost' in ads_df.columns else ads_df.columns[7]
                return pd.to_numeric(ads_df[col], errors='coerce').sum()
            else:
                ads_df = pd.read_excel(file, sheet_name='Ads Cost', usecols="H")
                return pd.to_numeric(ads_df.iloc[:, 0], errors='coerce').sum()

        same_ads_sum = get_ads_sum(same_month_file)
        next_ads_sum = get_ads_sum(next_month_file)

    except Exception as e:
        st.error(f"Error reading files: {e}")
        return None, None

    # --- Data Processing ---
    df_orders_raw = df_orders[["Sub Order No", "SKU", "Quantity"]].copy()
    df_orders_raw['Quantity'] = pd.to_numeric(df_orders_raw['Quantity'], errors='coerce').fillna(0)

    # Combine payment sheets for status tracking
    df_combined_pmt = pd.concat([df_same, df_next], ignore_index=True)
    df_combined_pmt['Final Settlement Amount'] = pd.to_numeric(df_combined_pmt['Final Settlement Amount'], errors='coerce').fillna(0)
    
    # --- HANDLE MULTI-STATUS LOGIC ---
    # 1. Convert Payment Date to datetime
    df_combined_pmt['Payment Date'] = pd.to_datetime(df_combined_pmt['Payment Date'], errors='coerce')
    # 2. Sort by Sub Order No and Date (Newest first)
    df_combined_pmt = df_combined_pmt.sort_values(by=['Sub Order No', 'Payment Date'], ascending=[True, False])
    # 3. Get the latest status and unique payment totals
    status_lookup = df_combined_pmt[['Sub Order No', 'Live Order Status']].drop_duplicates(subset=['Sub Order No'], keep='first')
    payment_lookup = df_combined_pmt.groupby('Sub Order No')['Final Settlement Amount'].sum().reset_index()

    # --- Merging Data ---
    df_orders_final = pd.merge(df_orders_raw, payment_lookup, on='Sub Order No', how='left')
    df_orders_final.rename(columns={'Final Settlement Amount': 'total_payment'}, inplace=True)
    
    df_orders_final = pd.merge(df_orders_final, status_lookup, on='Sub Order No', how='left')
    df_orders_final.rename(columns={'Live Order Status': 'status'}, inplace=True)

    # --- COST LOGIC ---
    cost_lookup = df_cost.iloc[:, :2].copy()
    cost_lookup.columns = ['SKU_Lookup', 'Cost_Value']
    df_orders_final['SKU'] = df_orders_final['SKU'].astype(str)
    cost_lookup['SKU_Lookup'] = cost_lookup['SKU_Lookup'].astype(str)
    df_orders_final = pd.merge(df_orders_final, cost_lookup, left_on='SKU', right_on='SKU_Lookup', how='left')

    # Condition: Only apply product cost to Delivered, Return, or Exchange (RTO is stock return)
    # You can customize this list based on your preference
    costly_statuses = ['Delivered', 'Return', 'Exchange']
    condition = df_orders_final['status'].str.strip().isin(costly_statuses)
    df_orders_final['cost_per_unit'] = np.where(condition, df_orders_final['Cost_Value'], 0)
    df_orders_final['actual_product_cost'] = df_orders_final['cost_per_unit'].fillna(0) * df_orders_final['Quantity']
    
    # Packaging Logic: Only apply to Dispatched orders (Not Cancelled or Unknown)
    dispatched_statuses = ['Delivered', 'Return', 'RTO', 'Exchange', 'Shipped']
    dispatch_cond = df_orders_final['status'].str.strip().isin(dispatched_statuses)
    df_orders_final['packaging_cost'] = np.where(dispatch_cond, packaging_cost_value, 0)

    # --- Calculate Final Stats ---
    total_payment_sum = df_orders_final['total_payment'].sum()
    total_actual_cost_sum = df_orders_final['actual_product_cost'].sum()
    total_packaging_sum = df_orders_final['packaging_cost'].sum()
    
    # Final Profit (Excluding Platform Recovery as requested)
    profit_loss_value = total_payment_sum - total_actual_cost_sum - total_packaging_sum - abs(same_ads_sum) - misc_cost_value

    status_series = df_orders_final['status'].fillna('Unknown').str.strip()
    
    stats = {
        "Total Payments": total_payment_sum,
        "Total Actual Cost": total_actual_cost_sum,
        "Total Packaging Cost": total_packaging_sum,
        "Same Month Ads Cost": abs(same_ads_sum),
        "Profit / Loss": profit_loss_value,
        "count_total": len(df_orders_final),
        "count_delivered": len(df_orders_final[status_series == 'Delivered']),
        "count_return": len(df_orders_final[status_series == 'Return']),
        "count_rto": len(df_orders_final[status_series == 'RTO']),
        "count_exchange": len(df_orders_final[status_series == 'Exchange']),
        "count_cancelled": len(df_orders_final[status_series == 'Cancelled']),
        "count_shipped": len(df_orders_final[status_series == 'Shipped']),
    }

    # --- Write to Excel ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_orders_final.to_excel(writer, sheet_name='Detailed Orders', index=False)
        summary_df = pd.DataFrame(list(stats.items()), columns=['Metric', 'Value'])
        summary_df.to_excel(writer, sheet_name='Summary Report', index=False)
    output.seek(0)
    
    return output, stats

# --- Streamlit App Interface ---
st.title("📊 Meesho Profit/Loss Dashboard")

st.markdown("### 1. Upload Files")
col_left, col_right = st.columns(2)
with col_left:
    orders_file = st.file_uploader("Upload September Orders (`orders.csv`)", type=['csv'])
    cost_file = st.file_uploader("Upload Product Cost Master", type=['csv', 'xlsx'])
with col_right:
    same_month_file = st.file_uploader("Upload Same Month Payment File", type=['csv', 'xlsx'])
    next_month_file = st.file_uploader("Upload Next Month Payment File", type=['csv', 'xlsx'])

st.markdown("### 2. Settings")
col_set1, col_set2 = st.columns(2)
with col_set1:
    pack_cost = st.number_input("Packaging Cost (per dispatched order)", value=3.5, step=0.5)
with col_set2:
    misc_cost = st.number_input("Other Miscellaneous Costs", value=0.0, step=100.0)

if orders_file and same_month_file and next_month_file and cost_file:
    if st.button("🚀 Process Dashboard", type="primary"):
        with st.spinner("Analyzing data..."):
            excel_data, stats = process_data(orders_file, same_month_file, next_month_file, cost_file, pack_cost, misc_cost)
            
            if excel_data:
                st.success("✅ Analysis Complete!")
                
                # Financial Metrics
                st.markdown("### 📈 Financial Summary")
                st.metric("NET PROFIT / LOSS", f"₹{stats['Profit / Loss']:,.2f}")
                
                f_col1, f_col2, f_col3, f_col4 = st.columns(4)
                f_col1.metric("Settlement Received", f"₹{stats['Total Payments']:,.2f}")
                f_col2.metric("Product Cost (COGS)", f"₹{stats['Total Actual Cost']:,.2f}")
                f_col3.metric("Packaging Total", f"₹{stats['Total Packaging Cost']:,.2f}")
                f_col4.metric("Ads Spent", f"₹{stats['Same Month Ads Cost']:,.2f}")
                
                st.divider()

                # Status Metrics
                st.markdown("### 📦 Order Status Breakdown")
                s_col1, s_col2, s_col3, s_col4, s_col5, s_col6 = st.columns(6)
                s_col1.metric("Total", stats['count_total'])
                s_col2.metric("Delivered", stats['count_delivered'])
                s_col3.metric("Return", stats['count_return'])
                s_col4.metric("RTO", stats['count_rto'])
                s_col5.metric("Exchange", stats['count_exchange'])
                s_col6.metric("Cancelled", stats['count_cancelled'])
                
                st.divider()
                st.download_button("⬇️ Download Final Report", data=excel_data, file_name="September_Profit_Report.xlsx", use_container_width=True, type="primary")
                st.balloons()
