import pandas as pd

df = pd.read_excel("data/transaksi_bersih.xlsx")

df["Tanggal Pesanan"] = pd.to_datetime(
    df["Tanggal Pesanan"],
    errors="coerce"
)

print(
    df.sort_values(
        "Tanggal Pesanan",
        ascending=False
    )[["Tanggal Pesanan", "Nama Pelanggan"]]
    .head(20)
)