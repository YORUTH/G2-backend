from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from members.models import LockerAssignment, Locker


class Command(BaseCommand):
    help = "Expire overdue locker assignments and release cooled-down lockers."

    def handle(self, *args, **options):
        now = timezone.now()
        expired_count = 0
        released_count = 0

        overdue = LockerAssignment.objects.filter(
            status="active", expires_at__lte=now
        ).select_related("locker")

        for assignment in overdue:
            assignment.status = "expired"
            assignment.expired_at = now
            assignment.save(update_fields=["status", "expired_at"])

            assignment.locker.status = "cooldown"
            assignment.locker.cooldown_until = now + timedelta(days=3)
            assignment.locker.save(update_fields=["status", "cooldown_until"])
            expired_count += 1

        cooled_down = Locker.objects.filter(
            status="cooldown", cooldown_until__lte=now
        )

        for locker in cooled_down:
            locker.status = "available"
            locker.cooldown_until = None
            locker.save(update_fields=["status", "cooldown_until"])
            released_count += 1

        self.stdout.write(
            f"Done — {expired_count} assignment(s) expired, "
            f"{released_count} locker(s) released."
        )
