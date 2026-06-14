import pandas as pd

# =========================
# LOAD DATA BERSIH
# =========================

df = pd.read_excel(
    "data/transaksi_bersih.xlsx"
)

# =========================
# FORMAT DATA
# =========================

df["Tanggal"] = pd.to_datetime(
    df["Tanggal"]
)

df["Total Transaksi"] = pd.to_numeric(
    df["Total Transaksi"],
    errors="coerce"
)

today = pd.Timestamp.today().normalize()
future_rows = df["Tanggal"] > today
if future_rows.any():
    print(f"Mengabaikan {future_rows.sum()} transaksi bertanggal masa depan.")
    df = df[~future_rows].copy()

# =========================
# HITUNG RFM
# =========================

snapshot_date = today

rfm = df.groupby("Nama Customer").agg({
    "Tanggal": [
        "max",
        lambda x: (
            snapshot_date - x.max()
        ).days
    ],
    "Nama Customer": "count",
    "Total Transaksi": "sum"
})

rfm.columns = [
    "Last_Transaction_Date",
    "Recency",
    "Frequency",
    "Monetary"
]

# =========================
# HASIL
# =========================

print(rfm.head())

print("\nJumlah Customer:")
print(len(rfm))

rfm.to_excel(
    "data/rfm.xlsx"
)

print("\nFile rfm.xlsx berhasil dibuat")
