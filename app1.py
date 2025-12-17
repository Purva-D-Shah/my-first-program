import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# --- Configuration ---
st.set_page_config(
    page_title="Meesho Data Processor (Profit/Loss Calculator)",
    layout="wide",
    initial_sidebar_state="expanded"
)

def process_data(orders_file, same_month_file, next_month_file, cost_file, packaging_cost_value, misc_cost_value):
    """
    Reads files, processes logic, adds packaging cost, 
    calculates Ads Cost sums, calculates PROFIT, 
    creates summary sheet, and returns Excel & stats.
    """
    # 1. Read the input files
    try:
        # --- A. Read Orders ---
        df_orders = pd.read_csv(orders_file)

        # --- B. Read Order Payments (Sheet 1) ---
        excel_cols = ["Sub Order No", "Live Order Status", "Final Settlement Amount"]
        
        df_same = pd.read_excel(
            same_month_file,
            sheet_name='Order Payments',
            header=1, 
            usecols='A,F,L' 
        )
        df_next = pd.read_excel(
            next_month_file,
            sheet_name='Order Payments',
            header=1,
            usecols='A,F,L'
        )

        # --- C. Read Cost File ---
        if cost_file.name.endswith('.csv'):
            df_cost = pd.read_csv(cost_file)
        else:
            df_cost = pd.read_excel(cost_file)

        # --- D. Read Ads Cost (Sheet 3) ---
        same_month_file.seek(0)
        next_month_file.seek(0)

        try:
            df_same_ads = pd.read_excel(same_month_file, sheet_name='Ads Cost', usecols="H")
            same_ads_sum = pd.to_numeric(df_same_ads.iloc[:, 0], errors='coerce').sum()
        except Exception:
            same_ads_sum = 0
            
        try:
            df_next_ads = pd.read_excel(next_month_file, sheet_name='Ads Cost', usecols="H")
            next_ads_sum = pd.to_numeric(df_next_ads.iloc[:, 0], errors='coerce').sum()
        except Exception:
            next_ads_sum = 0

    except Exception as e:
        st.error(f"Error reading one or more files: {e}")
        return None, None

    # --- Data Processing ---
    
    # Standardize column names
    df_same.columns = excel_cols
    df_next.columns = excel_cols
    
    # Raw data sheets
    df_orders_raw = df_orders[["Sub Order No", "SKU", "Quantity"]].copy()
    
    # Ensure Quantity is numeric
    df_orders_raw['Quantity'] = pd.to_numeric(df_orders_raw['Quantity'], errors='coerce').fillna(0)

    df_same_sheet = df_same[excel_cols].copy()
    df_next_sheet = df_next[excel_cols].copy()
    
    # Create concatenated status data
    df_order_status = pd.concat([df_same_sheet, df_next_sheet], ignore_index=True)
    
    # Prepare Pivot Data
    def prepare_for_pivot(df):
        df['Final Settlement Amount'] = pd.to_numeric(
            df['Final Settlement Amount'], 
            errors='coerce' 
        ).fillna(0)
        return df
        
    df_same_pivot_data = prepare_for_pivot(df_same_sheet.copy())
    df_next_pivot_data = prepare_for_pivot(df_next_sheet.copy())

    # --- Pivot Tables ---
    df_pivot_same = pd.pivot_table(df_same_pivot_data, values='Final Settlement Amount', index=['Sub Order No'], aggfunc='sum').reset_index()
    df_pivot_same.rename(columns={'Final Settlement Amount': 'same month pay'}, inplace=True)
    
    df_pivot_next = pd.pivot_table(df_next_pivot_data, values='Final Settlement Amount', index=['Sub Order No'], aggfunc='sum').reset_index()
    df_pivot_next.rename(columns={'Final Settlement Amount': 'next month pay'}, inplace=True)


    # --- Merging Data ---
    df_orders_final = df_orders_raw.copy()
    
    # Merge Payment Data
    df_orders_final = pd.merge(df_orders_final, df_pivot_same[['Sub Order No', 'same month pay']], on='Sub Order No', how='left')
    df_orders_final = pd.merge(df_orders_final, df_pivot_next[['Sub Order No', 'next month pay']], on='Sub Order No', how='left')
    
    # Calculate 'total' pay
    df_orders_final['total'] = df_orders_final[['same month pay', 'next month pay']].sum(axis=1, skipna=True)
    
    # Merge 'status'
    status_lookup = df_order_status[['Sub Order No', 'Live Order Status']].drop_duplicates(subset=['Sub Order No'], keep='first')
    df_orders_final = pd.merge(df_orders_final, status_lookup, on='Sub Order No', how='left')
    df_orders_final.rename(columns={'Live Order Status': 'status'}, inplace=True)

    # --- COST LOGIC ---
    cost_lookup = df_cost.iloc[:, :2].copy()
    cost_lookup.columns = ['SKU_Lookup', 'Cost_Value'] 

    df_orders_final['SKU'] = df_orders_final['SKU'].astype(str)
    cost_lookup['SKU_Lookup'] = cost_lookup['SKU_Lookup'].astype(str)

    df_orders_final = pd.merge(df_orders_final, cost_lookup, left_on='SKU', right_on='SKU_Lookup', how='left')

    condition = df_orders_final['status'].str.strip().isin(['Delivered', 'Exchanged'])
    df_orders_final['cost'] = np.where(condition, df_orders_final['Cost_Value'], 0)
    df_orders_final['cost'] = df_orders_final['cost'].fillna(0)

    # Calculate 'actual cost'
    df_orders_final['actual cost'] = df_orders_final['cost'] * df_orders_final['Quantity']
    
    # --- PACKAGING COST ---
    df_orders_final['packaging cost'] = packaging_cost_value

    # Clean up temp cols
    df_orders_final.drop(columns=['SKU_Lookup', 'Cost_Value'], inplace=True)

    # --- Calculate Final Stats ---
    total_payment_sum = df_orders_final['total'].sum(skipna=True)
    total_cost_sum = df_orders_final['cost'].sum(skipna=True)
    total_actual_cost_sum = df_orders_final['actual cost'].sum(skipna=True)
    total_packaging_sum = df_orders_final['packaging cost'].sum(skipna=True)

    # --- CALCULATE PROFIT ---
    # Formula: Total Payments - Actual Cost - Packaging Cost - ABS(Same Month Ads Cost)
    profit_loss_value = total_payment_sum - total_actual_cost_sum - total_packaging_sum - abs(same_ads_sum)

    # Dictionary for easy access and return
    stats = {
        "Total Payments": total_payment_sum,
        "Total Cost": total_cost_sum,
        "Total Actual Cost": total_actual_cost_sum,
        "Total Packaging Cost": total_packaging_sum,
        "Same Month Ads Cost": same_ads_sum,
        "Next Month Ads Cost": next_ads_sum,
        "Miscellaneous Cost": misc_cost_value,
        "Profit / Loss": profit_loss_value
    }

    # --- Write to Excel ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        
        # --- Sheet 1: orders.csv ---
        df_orders_final.to_excel(writer, sheet_name='orders.csv', index=False)
        
        workbook = writer.book
        worksheet_orders = writer.sheets['orders.csv']
        summary_format = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#DDEBF7'})
        label_format = workbook.add_format({'bold': True})
        
        data_end_row = len(df_orders_final) + 1
        
        # Column Indices
        col_total = df_orders_final.columns.get_loc('total')
        col_cost = df_orders_final.columns.get_loc('cost')
        col_actual_cost = df_orders_final.columns.get_loc('actual cost')
        col_pack_cost = df_orders_final.columns.get_loc('packaging cost')
        
        # Write "GRAND TOTALS"
        worksheet_orders.write(data_end_row, col_total - 1, 'GRAND TOTALS', summary_format)
        worksheet_orders.write(data_end_row, col_total, total_payment_sum, summary_format)
        worksheet_orders.write(data_end_row, col_cost, total_cost_sum, summary_format)
        worksheet_orders.write(data_end_row, col_actual_cost, total_actual_cost_sum, summary_format)
        worksheet_orders.write(data_end_row, col_pack_cost, total_packaging_sum, summary_format)
        
        # Write Ads Cost
        worksheet_orders.write(data_end_row + 2, 0, 'Same Month Ads Cost', label_format)
        worksheet_orders.write(data_end_row + 2, 1, same_ads_sum)
        worksheet_orders.write(data_end_row + 3, 0, 'Next Month Ads Cost', label_format)
        worksheet_orders.write(data_end_row + 3, 1, next_ads_sum)

        
        # --- Sheet 2 "final sheet" (Summary) ---
        summary_df = pd.DataFrame(list(stats.items()), columns=['Metric', 'Value'])
        summary_df.to_excel(writer, sheet_name='final sheet', index=False)


        # --- Sheets 3-8 (Rest of Data) ---
        df_same_sheet.to_excel(writer, sheet_name='same month', index=False)
        df_next_sheet.to_excel(writer, sheet_name='next month', index=False)
        
        df_pivot_same.rename(columns={'same month pay': 'Total Final Settlement Amount (Same Month)'}, inplace=True)
        df_pivot_next.rename(columns={'next month pay': 'Total Final Settlement Amount (Next Month)'}, inplace=True)

        df_pivot_same.to_excel(writer, sheet_name='final same month', index=False)
        df_pivot_next.to_excel(writer, sheet_name='final next month', index=False)
        
        df_order_status.to_excel(writer, sheet_name='order status', index=False)
        df_cost.to_excel(writer, sheet_name='cost', index=False)

    output.seek(0)
    return output, stats


# --- Streamlit App Interface ---

st.title("📊 Dashboard Data Processor")

# --- PLACEHOLDER for Results (Top of Page) ---
results_container = st.container()

# --- Input Section ---
st.markdown("### 1. Upload & Settings")
st.info("Upload the required files here.")

# Create two columns for file uploads
col_left, col_right = st.columns(2)

# --- LEFT COLUMN: Orders & Cost ---
with col_left:
    st.markdown("**Orders & Cost Data**")
    orders_file = st.file_uploader("1. Upload orders file `orders.csv`", type=['csv'])
    cost_file = st.file_uploader("2. Upload `cost` file", type=['csv', 'xlsx'])

# --- RIGHT COLUMN: Same Month & Next Month ---
with col_right:
    st.markdown("**Payment & Ads Data**")
    same_month_file = st.file_uploader("3. Upload same month file `same.xlsx`", type=['xlsx'])
    next_month_file = st.file_uploader("4. Upload next month file `next.xlsx`", type=['xlsx'])

st.markdown("---")

# Settings in main dashboard
col_set1, col_set2 = st.columns(2)
with col_set1:
    pack_cost = st.number_input("Packaging Cost (per record)", value=5.0, step=0.5)
with col_set2:
    misc_cost = st.number_input("Miscellaneous Cost (e.g., lost product)", value=0.0, step=100.0)

st.markdown("---")

# Processing Logic
if orders_file and same_month_file and next_month_file and cost_file:
    # Button is in Main Area
    if st.button("🚀 Process Data and Generate Report", type="primary"):
        with st.spinner("Processing data..."):
            excel_data, stats = process_data(orders_file, same_month_file, next_month_file, cost_file, pack_cost, misc_cost)
            
            if excel_data and stats:
                
                # --- VISUALS: Populate Top Container (Above Inputs) ---
                with results_container:
                    st.success("✅ Processing Complete! See metrics below and download link in Sidebar.")
                    st.markdown("### 📈 Financial Summary")
                    
                    # Row 1: Key Metric (Profit Only)
                    pl_val = stats['Profit / Loss']
                    st.metric("PROFIT / LOSS", f"₹{pl_val:,.2f}", delta=f"{pl_val:,.2f}", delta_color="normal")
                    
                    st.divider()

                    # Detailed breakdown
                    col1, col2, col3, col4 = st.columns(4)
                    col5, col6, col7 = st.columns(3)
                    
                    with col1: st.metric("Total Payments", f"₹{stats['Total Payments']:,.2f}")
                    with col2: st.metric("Total Cost", f"₹{stats['Total Cost']:,.2f}")
                    with col3: st.metric("Total Actual Cost", f"₹{stats['Total Actual Cost']:,.2f}")
                    with col4: st.metric("Packaging Cost", f"₹{stats['Total Packaging Cost']:,.2f}")
                    
                    with col5: st.metric("Same Month Ads", f"₹{stats['Same Month Ads Cost']:,.2f}")
                    with col6: st.metric("Next Month Ads", f"₹{stats['Next Month Ads Cost']:,.2f}")
                    with col7: st.metric("Misc Cost", f"₹{stats['Miscellaneous Cost']:,.2f}")
                    
                    st.divider() # Separator before inputs start
                
                st.balloons()

                # --- DOWNLOAD: Move to Sidebar ---
                with st.sidebar:
                    st.header("Actions")
                    st.success("✅ Files Processed!")
                    st.markdown("### 📥 Download")
                    st.download_button(
                        label="⬇️ Download Excel Report",
                        data=excel_data,
                        file_name="Final_Processed_Order_Report.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
            else:
                st.error("Report generation failed due to a file processing error.")
else:
    st.warning("Please upload all four files above to enable processing.")

