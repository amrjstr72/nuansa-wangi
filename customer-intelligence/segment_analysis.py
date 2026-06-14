import pandas as pd

df = pd.read_excel(
    "data/customer_segment.xlsx"
)

print("\n=== JUMLAH CUSTOMER PER SEGMENT ===")
print(
    df["Segment"].value_counts()
)

print("\n=== RATA-RATA RFM PER SEGMENT ===")
print(
    df.groupby("Segment")[
        ["Recency", "Frequency", "Monetary"]
    ].mean()
)

print("\n=== TOTAL REVENUE PER SEGMENT ===")
print(
    df.groupby("Segment")[
        "Monetary"
    ].sum()
)

print("\n=== TOP 10 CUSTOMER BERDASARKAN MONETARY ===")

top_customer = (
    df.sort_values(
        by="Monetary",
        ascending=False
    )
    .head(10)
)

print(
    top_customer[
        [
            "Nama Customer",
            "Segment",
            "Frequency",
            "Monetary"
        ]
    ]
)