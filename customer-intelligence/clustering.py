import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans

# Load data RFM
rfm = pd.read_excel("data/rfm.xlsx")

# Ambil fitur RFM
X = rfm[["Recency", "Frequency", "Monetary"]]

# Standardisasi data
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# K-Means
kmeans = KMeans(
    n_clusters=4,
    random_state=42,
    n_init=10
)

rfm["Cluster"] = kmeans.fit_predict(X_scaled)

print("Jumlah customer per cluster:")
print(rfm["Cluster"].value_counts())

print("\nRata-rata tiap cluster:")
print(
    rfm.groupby("Cluster")[
        ["Recency", "Frequency", "Monetary"]
    ].mean()
)

# Label bisnis
cluster_labels = {
    0: "Potential Customer",
    1: "At Risk Customer",
    2: "Champion Customer",
    3: "Loyal Customer"
}

rfm["Segment"] = rfm["Cluster"].map(
    cluster_labels
)

# Simpan hasil akhir
rfm.to_excel(
    "data/customer_segment.xlsx",
    index=False
)

print("\nJumlah customer per segment:")
print(rfm["Segment"].value_counts())

print("\nFile customer_segment.xlsx berhasil dibuat")