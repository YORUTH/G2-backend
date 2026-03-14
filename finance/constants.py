from members.models import PACKAGE_DURATION

BASE_PRICE = 3000   # per month
CARD_FEE = 400      # new members only
LOCKER_FEE = 200    # if locker assigned on the same day as membership start


def calculate_amount(package_type, is_new, has_locker=False):
    """
    Returns (base_amount, card_fee, locker_fee, total).
    is_new  — True for new signups, False for renewals.
    has_locker — True if a locker was assigned the same day.
    """
    months = PACKAGE_DURATION.get(package_type, 0)
    base_amount = BASE_PRICE * months
    card_fee = CARD_FEE if is_new else 0
    locker_fee = LOCKER_FEE if has_locker else 0
    return base_amount, card_fee, locker_fee, base_amount + card_fee + locker_fee