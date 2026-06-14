import pandas as pd
df = pd.read_excel('nuansa_wangi/customer-intelligence/data/rfm.xlsx')
print('Columns:', df.columns.tolist())
print(df.head(3).to_string())
