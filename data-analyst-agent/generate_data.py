import csv
import random
from datetime import date, timedelta

random.seed(42)

products = ["Laptop", "Monitor", "Keyboard", "Mouse", "Headset", "Webcam", "Desk Chair", "USB Hub"]
regions = ["North", "South", "East", "West"]
reps = ["Alice", "Bob", "Carol", "David", "Eva"]

start = date(2024, 1, 1)

rows = []
for i in range(200):
    d = start + timedelta(days=random.randint(0, 364))
    product = random.choice(products)
    region = random.choice(regions)
    rep = random.choice(reps)
    units = random.randint(1, 20)
    unit_price = round(random.uniform(20, 1500), 2)
    revenue = round(units * unit_price, 2)
    rows.append([i + 1, d.isoformat(), product, region, rep, units, unit_price, revenue])

with open("sales_data.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["id", "date", "product", "region", "rep", "units", "unit_price", "revenue"])
    writer.writerows(rows)

print("Generated sales_data.csv with 200 rows")
