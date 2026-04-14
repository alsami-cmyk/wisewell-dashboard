"""
Wisewell — Failed Payments CRM
────────────────────────────────
Multi-analyst CRM backed by Google Sheets.
All edits (status, notes, follow-up dates, call logs) persist in real time.
Auto-refreshes every 60 seconds.

Run: streamlit run dashboard.py  (navigate to "Failed Payments CRM" in sidebar)
"""

import json
import os
import time
import tempfile
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from streamlit_autorefresh import st_autorefresh

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Failed Payments CRM — Wisewell",
    page_icon="💳",
    layout="wide",
)

TODAY = date.today().isoformat()

# ─── CONSTANTS ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_sheet_id():
    """Get Sheet ID from st.secrets (cloud) or local config file."""
    try:
        return st.secrets["google"]["sheet_id"]
    except Exception:
        cfg_path = os.path.join(BASE_DIR, ".sheet_config.json")
        with open(cfg_path) as f:
            return json.load(f)["sheet_id"]

SHEET_ID = _get_sheet_id()

ANALYSTS = ["", "Analyst 1", "Analyst 2", "Analyst 3", "Manager"]

STATUSES = {
    "NEW":           {"label": "🆕 New",               "color": "#a0a8c8"},
    "NO_ANSWER":     {"label": "📞 No Answer",          "color": "#ffa502"},
    "VOICEMAIL":     {"label": "📬 Voicemail Left",     "color": "#e8b94a"},
    "UNREACHABLE":   {"label": "📵 Unreachable",        "color": "#ff6b35"},
    "WRONG_NUM":     {"label": "❌ Wrong Number",       "color": "#ff4757"},
    "IN_PROGRESS":   {"label": "💬 In Progress",        "color": "#4f8ef7"},
    "PROMISE":       {"label": "🤝 Promise to Pay",     "color": "#2ed573"},
    "UPDATING_CARD": {"label": "💳 Updating Card",      "color": "#1abc9c"},
    "PARTIAL_PAY":   {"label": "💰 Partial Payment",    "color": "#52d8bc"},
    "RESOLVED":      {"label": "✅ Resolved",           "color": "#2ed573"},
    "HARDSHIP":      {"label": "😔 Hardship Claim",     "color": "#fd79a8"},
    "CANCEL_REQ":    {"label": "🚫 Cancel Request",     "color": "#ff4757"},
    "DISPUTED":      {"label": "⚠️ Disputed",          "color": "#ffa502"},
    "ESCALATED":     {"label": "⬆️ Escalated",         "color": "#9c88ff"},
    "WRITE_OFF":     {"label": "💀 Write-Off",          "color": "#c0504d"},
}
STATUS_LABELS = {k: v["label"] for k, v in STATUSES.items()}

PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}

# ─── GOOGLE SHEETS CLIENT ────────────────────────────────────────────────────
@st.cache_resource
def get_sheets_client():
    """Build Sheets client from st.secrets (cloud) or local token file."""
    try:
        # Streamlit Cloud — credentials stored in secrets
        token_info = json.loads(st.secrets["google"]["token_json"])
        creds = Credentials.from_authorized_user_info(token_info)
    except Exception:
        # Local — read from token.json file
        token_file = os.path.join(BASE_DIR, "token.json")
        creds = Credentials.from_authorized_user_file(token_file)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("sheets", "v4", credentials=creds)

def sheet_read(range_: str) -> list[list]:
    svc = get_sheets_client()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=range_
    ).execute()
    return result.get("values", [])

def sheet_write(range_: str, values: list[list]):
    svc = get_sheets_client()
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=range_,
        valueInputOption="RAW", body={"values": values}
    ).execute()

def sheet_append(range_: str, values: list[list]):
    svc = get_sheets_client()
    svc.spreadsheets().values().append(
        spreadsheetId=SHEET_ID, range=range_,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values}
    ).execute()

# ─── DATA LOADING ────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_customers() -> pd.DataFrame:
    rows = sheet_read("customers!A:M")
    if not rows:
        return pd.DataFrame()
    headers = rows[0]
    df = pd.DataFrame(rows[1:], columns=headers)
    df["monthly_fee"]  = pd.to_numeric(df["monthly_fee"],  errors="coerce").fillna(0)
    df["est_debt"]     = pd.to_numeric(df["est_debt"],     errors="coerce").fillna(0)
    df["months_unpaid"]= pd.to_numeric(df["months_unpaid"],errors="coerce").fillna(0)
    df["days_since"]   = pd.to_numeric(df["days_since"],   errors="coerce").fillna(0)
    return df

@st.cache_data(ttl=30)
def load_crm_state() -> pd.DataFrame:
    rows = sheet_read("crm_state!A:J")
    if not rows or len(rows) < 2:
        return pd.DataFrame(columns=["email","status","last_called","follow_up",
                                     "call_count","notes","assigned_to",
                                     "promise_date","promise_amount","updated_at"])
    headers = rows[0]
    df = pd.DataFrame(rows[1:], columns=headers)
    df["call_count"] = pd.to_numeric(df.get("call_count", 0), errors="coerce").fillna(0).astype(int)
    return df

def get_merged_df() -> pd.DataFrame:
    customers = load_customers()
    state     = load_crm_state()
    if customers.empty:
        return pd.DataFrame()
    merged = customers.merge(state, on="email", how="left")
    # Fill defaults for new customers with no CRM state
    merged["status"]       = merged["status"].fillna("NEW")
    merged["last_called"]  = merged["last_called"].fillna("")
    merged["follow_up"]    = merged["follow_up"].fillna("")
    merged["call_count"]   = merged["call_count"].fillna(0).astype(int)
    merged["notes"]        = merged["notes"].fillna("")
    merged["assigned_to"]  = merged["assigned_to"].fillna("")
    merged["promise_date"] = merged["promise_date"].fillna("")
    merged["promise_amount"]= merged["promise_amount"].fillna("")
    merged["updated_at"]   = merged["updated_at"].fillna("")
    return merged

# ─── CRM STATE WRITE ─────────────────────────────────────────────────────────
def upsert_crm_row(email: str, updates: dict):
    """Upsert a single customer's CRM state back to Google Sheets."""
    rows = sheet_read("crm_state!A:J")
    headers = rows[0] if rows else ["email","status","last_called","follow_up",
                                    "call_count","notes","assigned_to",
                                    "promise_date","promise_amount","updated_at"]
    # Find existing row index (1-based, row 1 = header)
    row_idx = None
    for i, row in enumerate(rows[1:], start=2):
        if row and row[0] == email:
            row_idx = i
            break

    # Build full row values
    def get_val(field):
        return str(updates.get(field, ""))

    now = datetime.utcnow().isoformat(timespec="seconds")
    row_vals = [
        email,
        get_val("status"),
        get_val("last_called"),
        get_val("follow_up"),
        get_val("call_count"),
        get_val("notes"),
        get_val("assigned_to"),
        get_val("promise_date"),
        get_val("promise_amount"),
        now,
    ]

    if row_idx:
        sheet_write(f"crm_state!A{row_idx}:J{row_idx}", [row_vals])
    else:
        sheet_append("crm_state!A:J", [row_vals])

    # Invalidate cache
    load_crm_state.clear()

def log_sync_event(event: str, details: str):
    now = datetime.utcnow().isoformat(timespec="seconds")
    sheet_append("sync_log!A:C", [[now, event, details]])

# ─── STYLE ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0d0f18; }
[data-testid="stSidebar"] { background: #161925; }
.block-container { padding-top: 1.5rem; max-width: 100%; }
h1, h2, h3 { color: #e2e5f0 !important; }
.stMetric label { color: #6b7394 !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.8px; }
.stMetric [data-testid="metric-value"] { font-size: 22px !important; font-weight: 700 !important; }
div[data-testid="column"] > div { border-right: 1px solid #272b3d; padding-right: 12px; }
.crm-row { border-bottom: 1px solid #1e2235; padding: 8px 0; }
.status-pill {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 600; cursor: default;
}
.priority-p1 { color: #ff4757; background: rgba(255,71,87,0.12); border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 700; }
.priority-p2 { color: #ffa502; background: rgba(255,165,2,0.12); border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 700; }
.priority-p3 { color: #2ed573; background: rgba(46,213,115,0.10); border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 700; }
.stButton button { background: #1e2235; border: 1px solid #272b3d; color: #a0a8c8; font-size: 12px; }
.stButton button:hover { border-color: #4f8ef7; color: #4f8ef7; }
.debt-high  { color: #ff4757 !important; font-weight: 700; }
.debt-med   { color: #ffa502 !important; font-weight: 700; }
[data-testid="stDataFrameResizable"] { border: 1px solid #272b3d; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── AUTO-REFRESH ─────────────────────────────────────────────────────────────
st_autorefresh(interval=60_000, key="crm_refresh")

# ─── HEADER ──────────────────────────────────────────────────────────────────
st.markdown("## 💳 Failed Payments CRM")
st.caption(f"All edits sync to Google Sheets in real time · Auto-refreshes every 60s · Today: {TODAY}")

# ─── LOAD DATA ───────────────────────────────────────────────────────────────
with st.spinner("Loading from Google Sheets…"):
    df = get_merged_df()

if df.empty:
    st.error("No data found. Run the sync script first.")
    st.stop()

# ─── TOP STATS ───────────────────────────────────────────────────────────────
total       = len(df)
p1_count    = (df["priority"] == "P1").sum()
today_fu    = (df["follow_up"] == TODAY).sum()
promises    = (df["status"] == "PROMISE").sum()
resolved    = (df["status"] == "RESOLVED").sum()
recovered   = df[df["status"] == "RESOLVED"]["est_debt"].sum()
unworked_p1 = ((df["priority"] == "P1") & (df["status"] == "NEW")).sum()
total_debt  = df["est_debt"].sum()

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Total Accounts",  total)
c2.metric("P1 Critical",     p1_count,   delta=f"{unworked_p1} uncontacted", delta_color="inverse")
c3.metric("Today's Follow-ups", today_fu)
c4.metric("Promises Pending",promises)
c5.metric("Resolved",        resolved)
c6.metric("AED Recovered",   f"AED {recovered:,.0f}" if recovered else "0")
c7.metric("Portfolio Debt",  f"AED {total_debt:,.0f}")

# ─── STATUS BREAKDOWN CHIPS ──────────────────────────────────────────────────
status_counts = df["status"].value_counts()
chip_html = " &nbsp;".join([
    f'<span style="background:rgba(255,255,255,0.05);border:1px solid #272b3d;border-radius:6px;'
    f'padding:3px 10px;font-size:11px;color:{STATUSES.get(k,{}).get("color","#888")};font-weight:600;">'
    f'{STATUS_LABELS.get(k, k)} <strong>{v}</strong></span>'
    for k, v in status_counts.items()
])
st.markdown(f"<div style='margin: 8px 0 16px;'>{chip_html}</div>", unsafe_allow_html=True)
st.divider()

# ─── FILTERS ─────────────────────────────────────────────────────────────────
col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns([2, 2, 2, 2, 3])

with col_f1:
    priority_filter = st.selectbox("Priority", ["All", "P1 Critical", "P2 High", "P3 Monitor"])
with col_f2:
    stage_filter = st.selectbox("Stage", ["All", "PAST_AUTO_FLOW", "IN_AUTO_FLOW", "VARIANT_ERROR"])
with col_f3:
    status_filter = st.selectbox("Status", ["All"] + list(STATUS_LABELS.values()))
with col_f4:
    view_filter = st.selectbox("Quick View", ["All", "📅 Today's Follow-ups", "🤝 Promises",
                                               "🆕 Uncontacted P1", "⚠️ Overdue Follow-ups"])
with col_f5:
    search = st.text_input("🔍 Search name, email, phone, city…", placeholder="Type to search…")

assigned_filter = st.selectbox("Assigned To", ["All"] + [a for a in ANALYSTS if a])

# ─── APPLY FILTERS ───────────────────────────────────────────────────────────
fdf = df.copy()

if priority_filter == "P1 Critical":   fdf = fdf[fdf["priority"] == "P1"]
elif priority_filter == "P2 High":     fdf = fdf[fdf["priority"] == "P2"]
elif priority_filter == "P3 Monitor":  fdf = fdf[fdf["priority"] == "P3"]

if stage_filter != "All":
    fdf = fdf[fdf["stage"] == stage_filter]

if status_filter != "All":
    key = next((k for k, v in STATUS_LABELS.items() if v == status_filter), None)
    if key: fdf = fdf[fdf["status"] == key]

if view_filter == "📅 Today's Follow-ups":
    fdf = fdf[fdf["follow_up"] == TODAY]
elif view_filter == "🤝 Promises":
    fdf = fdf[fdf["status"] == "PROMISE"]
elif view_filter == "🆕 Uncontacted P1":
    fdf = fdf[(fdf["priority"] == "P1") & (fdf["status"] == "NEW")]
elif view_filter == "⚠️ Overdue Follow-ups":
    fdf = fdf[(fdf["follow_up"] != "") & (fdf["follow_up"] < TODAY)]

if assigned_filter != "All":
    fdf = fdf[fdf["assigned_to"] == assigned_filter]

if search:
    mask = (
        fdf["name"].str.contains(search, case=False, na=False) |
        fdf["email"].str.contains(search, case=False, na=False) |
        fdf["phone"].str.contains(search, case=False, na=False) |
        fdf["city"].str.contains(search, case=False, na=False) |
        fdf["notes"].str.contains(search, case=False, na=False)
    )
    fdf = fdf[mask]

# Sort: P1 first, then by debt
fdf = fdf.sort_values(
    by=["priority", "est_debt"],
    key=lambda col: col.map(PRIORITY_ORDER) if col.name == "priority" else col,
    ascending=[True, False]
).reset_index(drop=True)

st.caption(f"Showing **{len(fdf)}** of {total} accounts")

# ─── EDITABLE CRM TABLE ──────────────────────────────────────────────────────
st.markdown("### Account List")
st.caption("Edit Status, Follow-up, Notes, Assigned To and Call Count directly. Changes save to Google Sheets on click.")

# Prepare display columns for st.data_editor
edit_cols = ["priority", "name", "phone", "city", "est_debt", "days_since",
             "stage", "status", "last_called", "follow_up",
             "call_count", "notes", "assigned_to", "promise_date"]

display_df = fdf[edit_cols + ["email"]].copy()
display_df["status_label"] = display_df["status"].map(lambda k: STATUS_LABELS.get(k, k))
display_df = display_df.drop(columns=["status"])

# Column config for data editor
col_config = {
    "priority":    st.column_config.SelectboxColumn("Pri", options=["P1","P2","P3"], width="small"),
    "name":        st.column_config.TextColumn("Name", width="medium", disabled=True),
    "phone":       st.column_config.TextColumn("Phone", width="medium", disabled=True),
    "city":        st.column_config.TextColumn("City", width="small", disabled=True),
    "est_debt":    st.column_config.NumberColumn("Est Debt AED", format="AED %.0f", width="small", disabled=True),
    "days_since":  st.column_config.NumberColumn("Days", width="small", disabled=True),
    "stage":       st.column_config.TextColumn("Stage", width="small", disabled=True),
    "status_label":st.column_config.SelectboxColumn(
        "Status", width="medium",
        options=list(STATUS_LABELS.values()),
    ),
    "last_called": st.column_config.DateColumn("Last Called", width="small", format="YYYY-MM-DD"),
    "follow_up":   st.column_config.DateColumn("Follow-up", width="small", format="YYYY-MM-DD"),
    "call_count":  st.column_config.NumberColumn("Calls", width="small", min_value=0, step=1),
    "notes":       st.column_config.TextColumn("Notes", width="large"),
    "assigned_to": st.column_config.SelectboxColumn("Assigned To", options=ANALYSTS, width="small"),
    "promise_date":st.column_config.DateColumn("Promise Date", width="small", format="YYYY-MM-DD"),
    "email":       st.column_config.TextColumn("Email", width="medium", disabled=True),
}

edited_df = st.data_editor(
    display_df,
    column_config=col_config,
    use_container_width=True,
    hide_index=True,
    height=600,
    key="crm_editor",
)

# ─── DETECT AND SAVE CHANGES ─────────────────────────────────────────────────
if st.session_state.get("crm_editor"):
    changes = st.session_state["crm_editor"]
    edited_rows = changes.get("edited_rows", {})

    if edited_rows:
        save_count = 0
        for row_idx_str, row_changes in edited_rows.items():
            row_idx = int(row_idx_str)
            email = display_df.iloc[row_idx]["email"]

            # Get current full state for this customer
            state_row = load_crm_state()
            cur = state_row[state_row["email"] == email]
            base = cur.iloc[0].to_dict() if not cur.empty else {}

            # Apply changes
            updates = {
                "status":       base.get("status", "NEW"),
                "last_called":  str(base.get("last_called", "")),
                "follow_up":    str(base.get("follow_up", "")),
                "call_count":   int(base.get("call_count", 0)),
                "notes":        base.get("notes", ""),
                "assigned_to":  base.get("assigned_to", ""),
                "promise_date": base.get("promise_date", ""),
                "promise_amount": base.get("promise_amount", ""),
            }

            for field, value in row_changes.items():
                if field == "status_label":
                    # Map label back to key
                    status_key = next((k for k, v in STATUS_LABELS.items() if v == value), value)
                    updates["status"] = status_key
                elif field == "last_called" and value:
                    updates["last_called"] = str(value)[:10]
                elif field == "follow_up" and value:
                    updates["follow_up"] = str(value)[:10]
                elif field == "promise_date" and value:
                    updates["promise_date"] = str(value)[:10]
                elif field in updates:
                    updates[field] = value if value is not None else updates[field]

            upsert_crm_row(email, updates)
            save_count += 1

        if save_count:
            st.success(f"✅ Saved {save_count} change(s) to Google Sheets")
            load_crm_state.clear()
            time.sleep(0.5)
            st.rerun()

# ─── CUSTOMER DETAIL PANEL ───────────────────────────────────────────────────
st.divider()
st.markdown("### 🔍 Customer Detail & Call Logger")
st.caption("Select a customer to log calls, set promise details, and add detailed notes.")

selected_email = st.selectbox(
    "Select customer",
    [""] + list(fdf["email"].values),
    format_func=lambda e: next((f"{r['name']} ({e}) — AED {r['est_debt']:,.0f} — {r['days_since']}d"
                                for _, r in fdf[fdf["email"]==e].iterrows()), e) if e else "— Select —"
)

if selected_email:
    row = fdf[fdf["email"] == selected_email].iloc[0]
    state_df = load_crm_state()
    state_row = state_df[state_df["email"] == selected_email]
    cur_state = state_row.iloc[0].to_dict() if not state_row.empty else {}

    c1, c2 = st.columns([2, 1])

    with c1:
        with st.form(key=f"detail_{selected_email}"):
            st.markdown(f"**{row['name']}** · {row['email']} · {row['phone']} · {row['city']}")
            st.caption(f"Priority: **{row['priority']}** · Stage: **{row['stage']}** · "
                       f"Est Debt: **AED {row['est_debt']:,.0f}** · "
                       f"First failed: **{row['earliest_charge']}** ({row['days_since']}d ago) · "
                       f"Error: **{row['error_type']}**")

            col_s1, col_s2, col_s3 = st.columns(3)
            with col_s1:
                new_status = st.selectbox("Status", list(STATUS_LABELS.values()),
                    index=list(STATUS_LABELS.keys()).index(cur_state.get("status", "NEW"))
                          if cur_state.get("status","NEW") in STATUS_LABELS else 0)
            with col_s2:
                new_assigned = st.selectbox("Assigned To", ANALYSTS,
                    index=ANALYSTS.index(cur_state.get("assigned_to","")) if cur_state.get("assigned_to","") in ANALYSTS else 0)
            with col_s3:
                new_call_count = st.number_input("Total Calls Made", min_value=0,
                    value=int(cur_state.get("call_count", 0)), step=1)

            col_d1, col_d2 = st.columns(2)
            with col_d1:
                lc_val = cur_state.get("last_called","")
                new_last_called = st.date_input("Last Called Date",
                    value=date.fromisoformat(lc_val) if lc_val else None,
                    format="YYYY-MM-DD")
            with col_d2:
                fu_val = cur_state.get("follow_up","")
                new_follow_up = st.date_input("Next Follow-up",
                    value=date.fromisoformat(fu_val) if fu_val else None,
                    format="YYYY-MM-DD")

            # Promise section (shown when status = PROMISE)
            status_key = next((k for k, v in STATUS_LABELS.items() if v == new_status), "NEW")
            if status_key == "PROMISE":
                st.markdown("**🤝 Promise Details**")
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    pd_val = cur_state.get("promise_date","")
                    new_promise_date = st.date_input("Promise Date",
                        value=date.fromisoformat(pd_val) if pd_val else None,
                        format="YYYY-MM-DD")
                with col_p2:
                    new_promise_amount = st.number_input(
                        f"Promise Amount (AED) [full = {row['est_debt']:,.0f}]",
                        min_value=0.0, value=float(cur_state.get("promise_amount") or 0),
                        step=100.0)
            else:
                new_promise_date = cur_state.get("promise_date","")
                new_promise_amount = cur_state.get("promise_amount","")

            new_notes = st.text_area("Notes (call outcomes, customer responses, agreements…)",
                value=cur_state.get("notes",""), height=120,
                placeholder="e.g. Customer said card expired, will update by Friday. Speaks Arabic. Promised AED 500 by 2026-04-10.")

            save_btn = st.form_submit_button("💾 Save Changes", type="primary")

        if save_btn:
            updates = {
                "status":         status_key,
                "last_called":    str(new_last_called) if new_last_called else "",
                "follow_up":      str(new_follow_up) if new_follow_up else "",
                "call_count":     new_call_count,
                "notes":          new_notes,
                "assigned_to":    new_assigned,
                "promise_date":   str(new_promise_date) if new_promise_date and str(new_promise_date) != "" else "",
                "promise_amount": str(new_promise_amount) if new_promise_amount else "",
            }
            upsert_crm_row(selected_email, updates)
            log_sync_event("CRM_UPDATE", f"{selected_email} → {status_key}")
            st.success("✅ Saved to Google Sheets")
            load_crm_state.clear()
            st.rerun()

    with c2:
        st.markdown("**📋 Call Log**")
        st.caption("Each save auto-logs the status + timestamp.")

        log_rows = sheet_read("sync_log!A:C")
        if log_rows and len(log_rows) > 1:
            log_df = pd.DataFrame(log_rows[1:], columns=log_rows[0])
            cust_log = log_df[log_df["details"].str.contains(selected_email, na=False)]
            if not cust_log.empty:
                for _, lr in cust_log.tail(10).iloc[::-1].iterrows():
                    st.caption(f"🕐 `{lr['timestamp'][:16]}` — {lr['details'].replace(selected_email+' → ','')}")
            else:
                st.caption("No activity logged yet.")
        else:
            st.caption("No activity logged yet.")

# ─── EXPORT ─────────────────────────────────────────────────────────────────
st.divider()
export_df = fdf.copy()
export_df["status"] = export_df["status"].map(lambda k: STATUS_LABELS.get(k, k))
csv = export_df.to_csv(index=False).encode()
st.download_button(
    "⬇️ Export to CSV",
    data=csv,
    file_name=f"wisewell_crm_{TODAY}.csv",
    mime="text/csv",
)
