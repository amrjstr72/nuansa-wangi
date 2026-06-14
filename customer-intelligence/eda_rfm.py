import pandas as pd

rfm = pd.read_excel(
    "data/rfm.xlsx"
)

print("\n=== DESKRIPSI RFM ===")
print(rfm.describe())