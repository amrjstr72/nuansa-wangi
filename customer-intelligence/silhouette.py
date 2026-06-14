import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

# Load data
rfm = pd.read_excel("data/rfm.xlsx")

# Ambil fitur
X = rfm[["Recency", "Frequency", "Monetary"]]

# Standardisasi
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# KMeans
kmeans = KMeans(
    n_clusters=4,
    random_state=42,
    n_init=10
)

labels = kmeans.fit_predict(X_scaled)

score = silhouette_score(
    X_scaled,
    labels
)

print("Silhouette Score:")
print(score)