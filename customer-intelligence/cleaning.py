import pandas as pd

# Load data
df = pd.read_excel("data/transaksi_setahun.xlsx")

# Format tanggal
df["Tanggal"] = pd.to_datetime(
    df["Tanggal"]
)

# Format total transaksi
df["Total Transaksi"] = pd.to_numeric(
    df["Total Transaksi"],
    errors="coerce"
)

# Rapikan nama customer
df["Nama Customer"] = (
    df["Nama Customer"]
    .str.strip()
    .str.title()
)

# Hapus data kosong
df = df.dropna(
    subset=[
        "Tanggal",
        "Nama Customer"
    ]
)

# Lokasi
df["Lokasi"] = (
    df["Lokasi"]
    .replace("-", "Tidak Diketahui")
)

# No HP
df["No HP"] = (
    df["No HP"]
    .fillna("Tidak Diketahui")
    .replace("-", "Tidak Diketahui")
)

# Hapus customer tanpa identitas

df = df[
    ~df["Nama Customer"].isin([
        "-",
        "Tidak Diketahui",
        ""
    ])
]

print("Jumlah transaksi:")
print(len(df))

print("\nJumlah customer unik:")
print(df["Nama Customer"].nunique())

# Simpan hasil
df.to_excel(
    "data/transaksi_bersih.xlsx",
    index=False
)

print("\nFile transaksi_bersih.xlsx berhasil dibuat")