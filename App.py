import streamlit as st
import pandas as pd
import altair as alt
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

st.set_page_config(page_title="Stock Portfolio Tracker", page_icon="📈", layout="wide")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
TXN_SHEET = "Transactions"
DIV_SHEET = "Dividends"

TXN_COLUMNS = ["Date", "Type", "Ticker", "Currency", "Price", "Quantity", "Brokerage Fee", "Exchange Rate"]
DIV_COLUMNS = ["Date", "Ticker", "Currency", "Quantity Held", "Gross", "Withholding Tax", "Net"]


# ---------------- GOOGLE SHEETS CONNECTION ----------------
@st.cache_resource
def get_spreadsheet():
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(st.secrets["spreadsheet_id"])


def get_or_create_worksheet(name, columns):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(columns))
        ws.append_row(columns)
    return ws


def load_records(sheet_name, columns):
    ws = get_or_create_worksheet(sheet_name, columns)
    records = ws.get_all_records()
    return records


def append_record(sheet_name, columns, record):
    ws = get_or_create_worksheet(sheet_name, columns)
    ws.append_row([record.get(c, "") for c in columns])


def overwrite_sheet(sheet_name, columns, records):
    ws = get_or_create_worksheet(sheet_name, columns)
    ws.clear()
    ws.append_row(columns)
    if records:
        rows = [[r.get(c, "") for c in columns] for r in records]
        ws.append_rows(rows)


# ---------------- LOAD DATA (once per session) ----------------
if "transactions" not in st.session_state:
    st.session_state.transactions = load_records(TXN_SHEET, TXN_COLUMNS)
if "dividends" not in st.session_state:
    st.session_state.dividends = load_records(DIV_SHEET, DIV_COLUMNS)

st.title("📈 Stock Portfolio Tracker")
st.caption("Connected to Google Sheets — changes save automatically")


def get_holdings_snapshot(as_of_date=None):
    """Returns {(ticker, currency): {qty, avg_cost}} based on Buy/Sell history up to as_of_date (inclusive)."""
    holdings = {}
    if not st.session_state.transactions:
        return holdings
    df = pd.DataFrame(st.session_state.transactions)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Brokerage Fee"] = pd.to_numeric(df["Brokerage Fee"], errors="coerce").fillna(0)
    if as_of_date is not None:
        df = df[df["Date"] <= pd.to_datetime(as_of_date)]
    df = df.sort_values("Date")

    for (ticker, currency), group in df.groupby(["Ticker", "Currency"]):
        qty_held = 0.0
        avg_cost = 0.0
        for _, row in group.iterrows():
            if row["Type"] == "Buy":
                existing_value = qty_held * avg_cost
                buy_value = row["Quantity"] * row["Price"] + row["Brokerage Fee"]
                qty_held += row["Quantity"]
                avg_cost = (existing_value + buy_value) / qty_held if qty_held > 0 else 0
            else:
                sell_qty = min(row["Quantity"], qty_held)
                qty_held -= sell_qty
        holdings[(ticker, currency)] = {"qty": qty_held, "avg_cost": avg_cost}
    return holdings


tab_form, tab_dividend, tab_dashboard = st.tabs(["➕ Add Transaction", "💵 Add Dividend", "📊 Dashboard"])

# ---------------- ADD BUY/SELL ----------------
def _apply_ticker_pick():
    if st.session_state.get("ticker_pick"):
        st.session_state["ticker_input"] = st.session_state["ticker_pick"]

with tab_form:
    existing_tickers = sorted(set(t["Ticker"] for t in st.session_state.transactions)) if st.session_state.transactions else []

    if existing_tickers:
        st.selectbox(
            "Quick-pick a ticker you've used before (optional)",
            [""] + existing_tickers,
            key="ticker_pick",
            on_change=_apply_ticker_pick,
        )

    ticker_input = st.text_input("Ticker", placeholder="e.g. AAPL", key="ticker_input").upper()
    currency = st.selectbox("Currency", ["MYR", "USD"], key="currency_choice")

    with st.form("txn_form", clear_on_submit=True):
        txn_type = st.radio("Type", ["Buy", "Sell"], horizontal=True)

        col1, col2 = st.columns(2)
        with col1:
            txn_date = st.date_input("Date", value=date.today())
        with col2:
            st.text_input("Ticker (selected above)", value=ticker_input, disabled=True)

        col3, col4 = st.columns(2)
        with col3:
            st.text_input("Currency (selected above)", value=currency, disabled=True)
        with col4:
            price = st.number_input("Price per Share", min_value=0.0, step=0.01, format="%.4f")

        col5, col6 = st.columns(2)
        with col5:
            quantity = st.number_input("Quantity (Shares)", min_value=0.0, step=1.0, format="%.4f")
        with col6:
            brokerage_fee = st.number_input("Brokerage Fee", min_value=0.0, step=0.01, format="%.2f")

        if currency == "USD":
            exchange_rate = st.number_input(
                "Exchange Rate (1 USD = ? MYR)",
                min_value=0.0, step=0.0001, format="%.4f", value=4.7000
            )
        else:
            exchange_rate = 1.0

        gross = quantity * price
        st.markdown(f"**Gross Amount: {currency} {gross:,.2f}**  |  **+ Fee: {currency} {brokerage_fee:,.2f}**")

        submitted = st.form_submit_button("Add Transaction", use_container_width=True)

        if submitted:
            ticker = ticker_input
            if not ticker or quantity <= 0 or price <= 0:
                st.error("Please fill in ticker, quantity, and price.")
            else:
                record = {
                    "Date": str(txn_date),
                    "Type": txn_type,
                    "Ticker": ticker,
                    "Currency": currency,
                    "Price": price,
                    "Quantity": quantity,
                    "Brokerage Fee": brokerage_fee,
                    "Exchange Rate": exchange_rate,
                }
                try:
                    append_record(TXN_SHEET, TXN_COLUMNS, record)
                    st.session_state.transactions.append(record)
                    st.success(f"{txn_type} recorded: {quantity} shares of {ticker} @ {currency} {price:,.2f}")
                except Exception as e:
                    st.error(f"Failed to save to Google Sheet: {e}")

    if st.session_state.transactions:
        st.subheader("Transaction log (latest 5)")
        df_log_all = pd.DataFrame(st.session_state.transactions[::-1]).reset_index(drop=True)
        st.dataframe(df_log_all.head(5), use_container_width=True)

        with st.expander("Edit full transaction history"):
            st.caption("Edit any cell directly, or use the trash icon on a row to delete it. Click Save to sync to Google Sheets.")
            edited_log = st.data_editor(
                df_log_all,
                use_container_width=True,
                num_rows="dynamic",
                key="txn_editor",
                column_config={
                    "Type": st.column_config.SelectboxColumn(options=["Buy", "Sell"]),
                    "Currency": st.column_config.SelectboxColumn(options=["USD", "MYR"]),
                },
            )
            if st.button("Save changes", key="save_txn"):
                new_records = edited_log.iloc[::-1].to_dict("records")
                try:
                    overwrite_sheet(TXN_SHEET, TXN_COLUMNS, new_records)
                    st.session_state.transactions = new_records
                    st.success("Transaction log updated in Google Sheets.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save to Google Sheet: {e}")

# ---------------- ADD DIVIDEND ----------------
with tab_dividend:
    if not st.session_state.transactions:
        st.info("You need at least one Buy transaction before logging a dividend.")
    else:
        div_date = st.date_input("Date", value=date.today(), key="div_date")

        holdings_as_of = get_holdings_snapshot(as_of_date=div_date)
        held_keys = [k for k, v in holdings_as_of.items() if v["qty"] > 0]

        if not held_keys:
            st.warning(f"You held no shares as of {div_date}. Check the date, or add the Buy transaction first.")
        else:
            options = [f"{t} ({c})" for t, c in held_keys]
            choice = st.selectbox("Ticker", options, key="div_ticker_choice")
            idx = options.index(choice)
            sel_ticker, sel_currency = held_keys[idx]
            qty_held = holdings_as_of[(sel_ticker, sel_currency)]["qty"]

            st.caption(f"You held **{qty_held:,.4f} shares** of {sel_ticker} ({sel_currency}) as of {div_date}")

            with st.form("div_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    gross_div = st.number_input(f"Gross Dividend ({sel_currency})", min_value=0.0, step=0.01, format="%.2f")
                with col2:
                    net_div = st.number_input(f"Net Dividend ({sel_currency})", min_value=0.0, step=0.01, format="%.2f")

                withheld = gross_div - net_div
                if gross_div > 0:
                    st.caption(f"Implied tax/fees withheld: {sel_currency} {withheld:,.2f}")

                submitted = st.form_submit_button("Add Dividend", use_container_width=True)

                if submitted:
                    if net_div <= 0:
                        st.error("Please enter at least the net dividend amount.")
                    else:
                        record = {
                            "Date": str(div_date),
                            "Ticker": sel_ticker,
                            "Currency": sel_currency,
                            "Quantity Held": qty_held,
                            "Gross": round(gross_div, 2),
                            "Withholding Tax": round(withheld, 2),
                            "Net": round(net_div, 2),
                        }
                        try:
                            append_record(DIV_SHEET, DIV_COLUMNS, record)
                            st.session_state.dividends.append(record)
                            st.success(f"Dividend recorded: {sel_currency} {net_div:,.2f} net from {sel_ticker}")
                        except Exception as e:
                            st.error(f"Failed to save to Google Sheet: {e}")

    if st.session_state.dividends:
        st.subheader("Dividend log")
        st.caption("Edit any cell directly, or use the trash icon on a row to delete it. Click Save to sync to Google Sheets.")
        df_div_log = pd.DataFrame(st.session_state.dividends[::-1]).reset_index(drop=True)
        edited_div_log = st.data_editor(
            df_div_log,
            use_container_width=True,
            num_rows="dynamic",
            key="div_editor",
            column_config={
                "Currency": st.column_config.SelectboxColumn(options=["USD", "MYR"]),
            },
        )
        if st.button("Save changes", key="save_div"):
            new_records = edited_div_log.iloc[::-1].to_dict("records")
            try:
                overwrite_sheet(DIV_SHEET, DIV_COLUMNS, new_records)
                st.session_state.dividends = new_records
                st.success("Dividend log updated in Google Sheets.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save to Google Sheet: {e}")

# ---------------- DASHBOARD ----------------
with tab_dashboard:
    if not st.session_state.transactions:
        st.info("Add some transactions first to see your dashboard.")
    else:
        df = pd.DataFrame(st.session_state.transactions)
        df["Date"] = pd.to_datetime(df["Date"])
        df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
        df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
        df["Brokerage Fee"] = pd.to_numeric(df["Brokerage Fee"], errors="coerce").fillna(0)
        df = df.sort_values("Date")

        div_df = pd.DataFrame(st.session_state.dividends) if st.session_state.dividends else pd.DataFrame(columns=["Currency", "Net", "Date"])
        if not div_df.empty:
            div_df["Net"] = pd.to_numeric(div_df["Net"], errors="coerce").fillna(0)

        for currency in df["Currency"].unique():
            st.subheader(f"💰 {currency} Portfolio")
            cdf = df[df["Currency"] == currency]

            currency_div_df = div_df[div_df["Currency"] == currency] if not div_df.empty else pd.DataFrame()
            if not currency_div_df.empty:
                monthly = currency_div_df.copy()
                monthly["Date"] = pd.to_datetime(monthly["Date"])
                monthly["Month"] = monthly["Date"].dt.strftime("%Y-%m")
                monthly_totals = monthly.groupby("Month")["Net"].sum().sort_index()
                st.caption(f"Monthly dividends earned ({currency}, net)")
                chart_df = monthly_totals.reset_index()
                chart_df.columns = ["Month", "Net"]
                month_chart = alt.Chart(chart_df).mark_bar().encode(
                    x=alt.X("Month:N", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Net:Q"),
                )
                st.altair_chart(month_chart, use_container_width=True)

            summary_rows = []
            for ticker, group in cdf.groupby("Ticker"):
                qty_held = 0.0
                avg_cost = 0.0
                realized_pnl = 0.0

                for _, row in group.iterrows():
                    if row["Type"] == "Buy":
                        total_existing = qty_held * avg_cost
                        buy_value = row["Quantity"] * row["Price"] + row["Brokerage Fee"]
                        qty_held += row["Quantity"]
                        avg_cost = (total_existing + buy_value) / qty_held if qty_held > 0 else 0
                    else:
                        sell_qty = min(row["Quantity"], qty_held)
                        proceeds = sell_qty * row["Price"] - row["Brokerage Fee"]
                        cost_basis = sell_qty * avg_cost
                        realized_pnl += proceeds - cost_basis
                        qty_held -= sell_qty

                summary_rows.append({
                    "Ticker": ticker,
                    "Quantity Held": round(qty_held, 4),
                    "Avg Cost (DCA)": round(avg_cost, 4),
                    "Total Cost (Held)": round(qty_held * avg_cost, 2),
                    "Realized Earn": round(realized_pnl, 2),
                })

            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df, use_container_width=True)

            total_dividends = div_df[div_df["Currency"] == currency]["Net"].sum() if not div_df.empty else 0.0

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("Total Invested (Held)", f"{currency} {summary_df['Total Cost (Held)'].sum():,.2f}")
            col_b.metric("Realized Earn", f"{currency} {summary_df['Realized Earn'].sum():,.2f}")
            col_c.metric("Dividends (Net)", f"{currency} {total_dividends:,.2f}")
            col_d.metric("Tickers Held", f"{(summary_df['Quantity Held'] > 0).sum()}")