from django.core.management.base import BaseCommand
from django.utils import timezone
from members.models import LockerAssignment


class Command(BaseCommand):
    help = "Expire overdue locker assignments."

    def handle(self, *args, **options):
        now = timezone.now()

        overdue = LockerAssignment.objects.filter(
            status="active", expires_at__lte=now
        )
        expired_count = overdue.update(status="expired", expired_at=now)

        self.stdout.write(f"Done — {expired_count} assignment(s) expired.")
