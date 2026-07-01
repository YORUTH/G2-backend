from django.core.management.base import BaseCommand

from members.models import MemberProfile


class Command(BaseCommand):
    help = (
        "Set payment_recorded_at from start_date for members missing it. "
        "Use when upgrading after the pending-delete finance fix. "
        "Adjust individual profiles in Django admin if payment was on an earlier pending day."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many profiles would be updated without saving.",
        )

    def handle(self, *args, **options):
        qs = MemberProfile.objects.filter(payment_recorded_at__isnull=True)
        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(f"Would backfill payment_recorded_at for {count} profile(s).")
            return

        updated = 0
        for profile in qs.iterator():
            profile.payment_recorded_at = profile.start_date
            profile.save(update_fields=["payment_recorded_at"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Backfilled payment_recorded_at for {updated} profile(s)."))
