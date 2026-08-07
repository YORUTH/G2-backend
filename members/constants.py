PACKAGE_PRICES = {
    "bronze": 3000,
    "chrome": 6000,
    "silver": 8000,
    "gold": 15000,
    "diamond": 25000,
    # Women-only placeholder variants (update prices later as needed).
    "bronze_women": 2500,
    "silver_women": 8000,
    "gold_women": 15000,
    "diamond_women": 25000,
}


def get_package_price(package_type):
    return PACKAGE_PRICES.get(package_type, 0)
