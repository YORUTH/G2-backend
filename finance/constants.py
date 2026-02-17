from members.models import PACKAGE_DURATION

BASE_PRICE = 3000
CARD_FEE = 400

def calculate_amount(package_type, is_new):
    k = PACKAGE_DURATION.get(package_type, 0)
    package_amount = BASE_PRICE * k
    card_fee = CARD_FEE if is_new else 0
    return package_amount, card_fee, package_amount + card_fee
