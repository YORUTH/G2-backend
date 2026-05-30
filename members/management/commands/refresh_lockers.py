from django.core.management.base import BaseCommand

from members.models import LockerAssignment, cleanup_locker_assignments


class Command(BaseCommand):
    help = "Expire overdue locker assignments and remove cooled-down records."

    def handle(self, *args, **options):
        before = LockerAssignment.objects.count()
        cleanup_locker_assignments()
        after = LockerAssignment.objects.count()

        self.stdout.write(
            f"Done — removed {before - after} stale assignment(s), "
            f"{after} remaining."
        )
