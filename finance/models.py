from django.db import models
from django.utils import timezone

TEMPORAL_DAILY_FEE = 500


class TemporalMember(models.Model):
    """
    A walk-in daily member. Each record = one day's payment of 500.
    Only the full name is recorded.
    """
    full_name = models.CharField(max_length=100)
    amount = models.PositiveIntegerField(default=TEMPORAL_DAILY_FEE)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.full_name} – {self.paid_at:%Y-%m-%d}"

# my pending isnt deletting members after they are activated means if a member is added the activated in the pending list it should be removed completely from that pending list but now when i try to add the same member into pending list after i activated it and after some time when i try to save it fails because the member is already saved or had not been completely removed from thedatabase in the first place so can you fix that tell me how you plan to fix it before implementing that and the only app folder you need in my opinion is members since it includes the pending system 