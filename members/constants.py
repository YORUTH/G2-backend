PACKAGE_PRICES = {
    "bronze": 3000,
    "silver": 8000,
    "gold": 15000,
    "diamond": 25000,
}


def get_package_price(package_type):
    return PACKAGE_PRICES.get(package_type, 0)
