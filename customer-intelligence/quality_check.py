import pandas as pd

df = pd.read_excel("data/transaksi_bersih.xlsx")

print("=== INFO DATA ===")
print(df.info())

print("\n=== DATA KOSONG ===")
print(df.isnull().sum())

print("\n=== KATEGORI CUSTOMER ===")
print(df["Kategori"].value_counts())

print("\n=== TOP 10 LOKASI ===")
print(df["Lokasi"].value_counts().head(10))

print("\n=== TOTAL TRANSAKSI NOL ===")
print(
    len(
        df[df["Total Transaksi"] <= 0]
    )
)