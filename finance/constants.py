from members.models import PACKAGE_DURATION
from members.constants import get_package_price

CARD_FEE = 400      # new members only
LOCKER_FEE = 200    # if locker assigned on the same day as membership start
BOXING_FEE = 800


def calculate_amount(package_type, is_new, has_locker=False, has_boxing=False):
    """
    Returns (base_amount, card_fee, locker_fee, boxing_fee, total).
    is_new  — True for new signups, False for renewals.
    has_locker — True if a locker was assigned the same day.
    has_boxing - True if the member opted for boxing.
    """
    months = PACKAGE_DURATION.get(package_type, 0)
    base_amount = get_package_price(package_type) if months else 0
    card_fee = CARD_FEE if is_new else 0
    locker_fee = LOCKER_FEE if has_locker else 0
    boxing_fee = BOXING_FEE if has_boxing else 0
    return base_amount, card_fee, locker_fee, boxing_fee, base_amount + card_fee + locker_fee + boxing_fee