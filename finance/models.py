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
