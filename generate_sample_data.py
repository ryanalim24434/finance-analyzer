"""
Generate a realistic fake transactions.csv so this project can be demoed
without anyone's real bank data.

Usage:
    python generate_sample_data.py              # 18 months, writes transactions.csv
    python generate_sample_data.py --months 24 --out mydata.csv

Schema produced:
    date      YYYY-MM-DD
    merchant  free text
    category  one of the categories below
    amount    positive = money out, negative = money in (income, refunds)
"""

import argparse
import csv
import random
from datetime import date, timedelta

SEED = 7  # fixed so the demo is reproducible

# category -> (merchants, typical amount range, roughly how many per month)
PROFILE = {
    "Groceries": (["Trader Joe's", "Wegmans", "Acme Markets", "Whole Foods"], (22, 145), 7),
    "Dining": (["Chipotle", "Local Diner", "Sushi Ya", "Pizzeria Vetri", "Starbucks"], (9, 68), 9),
    "Transport": (["SEPTA", "Uber", "Wawa Fuel", "Lyft"], (3, 55), 6),
    "Shopping": (["Amazon", "Target", "Uniqlo", "Best Buy"], (14, 210), 4),
    "Utilities": (["PECO", "Verizon Fios", "Aqua PA"], (45, 190), 3),
    "Subscriptions": (["Spotify", "Netflix", "iCloud", "GitHub"], (4, 23), 4),
    "Health": (["CVS Pharmacy", "Dr. Nguyen DDS", "Planet Fitness"], (12, 180), 2),
    "Entertainment": (["AMC Theatres", "Steam", "Ticketmaster"], (11, 95), 2),
}

RENT = 1650.0
PAYCHECK = -2450.0  # negative = money in


def month_starts(n_months: int):
    today = date.today().replace(day=1)
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(months)


def days_in(month_start: date) -> int:
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    total = (nxt - month_start).days
    today = date.today()
    if (month_start.year, month_start.month) == (today.year, today.month):
        return today.day  # don't invent transactions in the future
    return total


def generate(n_months: int):
    rng = random.Random(SEED)
    rows = []

    for i, ms in enumerate(month_starts(n_months)):
        dim = days_in(ms)
        # seasonal drift so trends and anomalies are actually visible
        drift = 1.0 + 0.012 * i
        december = ms.month == 12

        rows.append({"date": ms.isoformat(), "merchant": "Parkview Apartments",
                     "category": "Rent", "amount": round(RENT, 2)})
        for payday in (1, 15):
            rows.append({"date": ms.replace(day=payday).isoformat(), "merchant": "Employer Payroll",
                         "category": "Income", "amount": round(PAYCHECK / 2, 2)})

        for category, (merchants, (lo, hi), per_month) in PROFILE.items():
            count = max(1, int(rng.gauss(per_month, 1.2)))
            if december and category in ("Shopping", "Dining"):
                count = int(count * 2.1)  # planted holiday spike
            for _ in range(count):
                amount = rng.uniform(lo, hi) * drift
                if december and category == "Shopping":
                    amount *= 1.6
                rows.append({
                    "date": ms.replace(day=rng.randint(1, dim)).isoformat(),
                    "merchant": rng.choice(merchants),
                    "category": category,
                    "amount": round(amount, 2),
                })

        # occasional one-off large purchase, gives the anomaly tool something real
        if rng.random() < 0.18:
            category, merchant = rng.choice([
                ("Shopping", "Apple Store"),
                ("Transport", "Delta Air Lines"),
                ("Transport", "Mavis Tires"),
                ("Health", "Jefferson Health"),
            ])
            rows.append({
                "date": ms.replace(day=rng.randint(1, dim)).isoformat(),
                "merchant": merchant,
                "category": category,
                "amount": round(rng.uniform(420, 1400), 2),
            })

    rows.sort(key=lambda r: r["date"])
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=18)
    p.add_argument("--out", default="transactions.csv")
    args = p.parse_args()

    rows = generate(args.months)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "merchant", "category", "amount"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} transactions to {args.out}")


if __name__ == "__main__":
    main()