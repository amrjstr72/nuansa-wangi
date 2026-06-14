import pandas as pd

df = pd.read_excel("data/transaksi_bersih.xlsx")

print(
    df[df["Nama Customer"] == "-"]
)