import os
import pandas as pd
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
CURRENT_MONTH = "Apr-26"


def get_service():
    if not os.path.exists("token.json"):
        raise FileNotFoundError("token.json not found. Run auth.py first.")
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("sheets", "v4", credentials=creds)


def fetch_tab(service, tab_name):
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=tab_name)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return None
    max_cols = max(len(r) for r in rows)
    padded = [r + [""] * (max_cols - len(r)) for r in rows]
    header = padded[0]
    seen = {}
    clean_header = []
    for col in header:
        key = col.strip() or "Unnamed"
        seen[key] = seen.get(key, 0) + 1
        clean_header.append(f"{key}_{seen[key]}" if seen[key] > 1 else key)
    return pd.DataFrame(padded[1:], columns=clean_header)


def list_tabs(service):
    meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [sheet["properties"]["title"] for sheet in meta["sheets"]]
    print("\n=== Sheet Tabs ===")
    for i, name in enumerate(tabs, 1):
        print(f"  {i:2}. {name}")
    return tabs


def analyze_monthly_sales(service):
    print(f"\n=== Revenue Summary: 'Monthly Sales' (current month: {CURRENT_MONTH}) ===")
    df = fetch_tab(service, "Monthly Sales")
    if df is None:
        print("  No data found.")
        return

    label_col = df.columns[0]  # Row labels are in the first column

    if CURRENT_MONTH not in df.columns:
        print(f"  Column '{CURRENT_MONTH}' not found. Available months: {[c for c in df.columns if '-' in c]}")
        return

    # Show all row labels with their Apr-26 values
    df[CURRENT_MONTH] = pd.to_numeric(
        df[CURRENT_MONTH].astype(str).str.replace(r"[£$€,\s]", "", regex=True),
        errors="coerce"
    )

    relevant = df[[label_col, CURRENT_MONTH]].copy()
    relevant = relevant[relevant[label_col].str.strip() != ""]
    relevant = relevant[relevant[CURRENT_MONTH].notna() & (relevant[CURRENT_MONTH] != 0)]
    relevant = relevant.rename(columns={label_col: "Metric", CURRENT_MONTH: CURRENT_MONTH + " Value"})

    print(relevant.to_string(index=False))

    total = relevant[CURRENT_MONTH + " Value"].sum()
    print(f"\n  Sum of all non-zero rows for {CURRENT_MONTH}: {total:,.2f}")


def analyze_subscriber_base(service):
    print(f"\n=== Subscriber Base Analysis ===")
    df = fetch_tab(service, "Subscriber Base")
    if df is None:
        print("  No data found.")
        return

    print(f"  Rows: {len(df)}  |  Columns: {list(df.columns[:10])}{'...' if len(df.columns) > 10 else ''}")

    # Look for an ID column for duplicate check
    id_col = next(
        (c for c in df.columns if any(k in c.lower() for k in ["id", "subscription", "order", "ref"])),
        None
    )
    if id_col:
        dupes = df[df.duplicated(subset=[id_col], keep=False) & (df[id_col].str.strip() != "")]
        if not dupes.empty:
            print(f"\n  Duplicate '{id_col}' values ({len(dupes)} rows):")
            print(dupes[id_col].value_counts().head(10).to_string())
        else:
            print(f"  No duplicate '{id_col}' values found.")
    else:
        print("  No ID column detected.")

    # Missing values
    missing = {c: (df[c].isnull().sum() + (df[c].astype(str).str.strip() == "").sum())
               for c in df.columns}
    missing = {k: v for k, v in missing.items() if v > 0 and "Unnamed" not in k}
    if missing:
        print("\n  Columns with missing / blank values:")
        for col, count in missing.items():
            print(f"    - {col}: {count}")
    else:
        print("  No missing values detected.")


def analyze_marketing_spend(service):
    print(f"\n=== Marketing Spend Irregularities ===")
    df = fetch_tab(service, "Marketing Spend")
    if df is None:
        print("  No data found.")
        return

    print(f"  Rows: {len(df)}  |  Columns: {list(df.columns)}")

    missing = {c: (df[c].isnull().sum() + (df[c].astype(str).str.strip() == "").sum())
               for c in df.columns}
    missing = {k: v for k, v in missing.items() if v > 0 and "Unnamed" not in k}

    if missing:
        print("\n  Missing / blank values:")
        for col, count in missing.items():
            pct = round(count / len(df) * 100, 1)
            print(f"    - {col}: {count} missing ({pct}%)")
    else:
        print("  No missing values found.")

    # Flag any numeric columns with outliers (>3x the median)
    for col in df.columns:
        if "Unnamed" in col:
            continue
        numeric = pd.to_numeric(
            df[col].astype(str).str.replace(r"[£$€,\s]", "", regex=True),
            errors="coerce"
        ).dropna()
        if len(numeric) < 3:
            continue
        median = numeric.median()
        if median == 0:
            continue
        outliers = numeric[numeric > median * 3]
        if not outliers.empty:
            print(f"\n  Potential outliers in '{col}' (>3x median of {median:,.0f}):")
            for idx, val in outliers.items():
                print(f"    Row {idx + 2}: {val:,.0f}")


def main():
    service = get_service()
    list_tabs(service)
    analyze_monthly_sales(service)
    analyze_subscriber_base(service)
    analyze_marketing_spend(service)


if __name__ == "__main__":
    main()
