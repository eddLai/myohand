#%%
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

# data/ 在 repo 根目錄，所以路徑錨在這個檔案的上一層，從哪個 cwd 跑都一樣
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 讀取 CSV 檔案
file_path = DATA_DIR / "grandma's_hand.csvemg.csv"
column_names = ["Timestamp", "Channel_A", "Channel_B", "Channel_C", 
                "Channel_D", "Channel_E", "Channel_F", "Channel_G", "Channel_H"]

# 讀取 CSV，並直接指定欄位名稱
data = pd.read_csv(file_path, sep=r"\s+", header=None, names=column_names)

# 確保時間戳記為 datetime 格式（將 UNIX 時間轉為可讀格式）
data["Timestamp"] = pd.to_datetime(data["Timestamp"], unit='s')
# %%
