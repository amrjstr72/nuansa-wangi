import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt

# Load data RFM
rfm = pd.read_excel("data/rfm.xlsx")

# Ambil fitur
X = rfm[["Recency", "Frequency", "Monetary"]]

# Standardisasi
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# Hitung inertia
inertia = []

for k in range(1, 11):
    kmeans = KMeans(
        n_clusters=k,
        random_state=42,
        n_init=10
    )

    kmeans.fit(X_scaled)

    inertia.append(kmeans.inertia_)

# Plot
plt.figure(figsize=(8, 5))
plt.plot(range(1, 11), inertia, marker='o')

plt.title("Elbow Method")
plt.xlabel("Jumlah Cluster (K)")
plt.ylabel("Inertia")
plt.grid(True)

plt.show()