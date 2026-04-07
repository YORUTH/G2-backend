from members.models import PACKAGE_DURATION
from members.constants import get_package_price

CARD_FEE = 400

def calculate_amount(package_type, is_new):
    k = PACKAGE_DURATION.get(package_type, 0)
    package_amount = get_package_price(package_type) if k else 0
    card_fee = CARD_FEE if is_new else 0
    return package_amount, card_fee, package_amount + card_fee
