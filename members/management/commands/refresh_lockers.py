from django.core.management.base import BaseCommand

from members.models import LockerAssignment, cleanup_locker_assignments


class Command(BaseCommand):
    help = "Mark overdue locker assignments as expired."

    def handle(self, *args, **options):
        before = LockerAssignment.objects.filter(status="active").count()
        cleanup_locker_assignments()
        after = LockerAssignment.objects.filter(status="active").count()
        expired = before - after

        self.stdout.write(
            f"Done — expired {expired} assignment(s), "
            f"{after} still active."
        )
