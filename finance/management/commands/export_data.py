"""
Export all database tables to Excel (.xlsx) and/or CSV files.

Usage:
    python manage.py export_data                # exports both Excel + CSVs
    python manage.py export_data --format excel  # Excel only
    python manage.py export_data --format csv    # CSVs only
    python manage.py export_data --output /path  # custom output directory
"""

import csv
import os
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from accounts.models import User
from members.models import (
    MemberProfile,
    CheckIn,
    Locker,
    LockerAssignment,
    PendingMember,
    PendingMemberHistory,
)
from finance.models import TemporalMember
from staff.models import TrainerDietplan


# ── Table definitions ────────────────────────────────────────────────
# Each entry: (sheet_name, queryset_callable, column_specs)
# column_specs: list of (header, accessor)
#   accessor can be a string (field name) or a callable(obj) -> value

def _user_display(obj, field):
    """Resolve a ForeignKey to User into a readable string."""
    user = getattr(obj, field, None)
    if user is None:
        return ""
    return f"{user.first_name} {user.last_name} ({user.phone_number})"


TABLE_DEFS = [
    (
        "Users",
        lambda: User.objects.all().order_by("id"),
        [
            ("ID", "id"),
            ("Phone Number", lambda o: str(o.phone_number)),
            ("First Name", "first_name"),
            ("Last Name", "last_name"),
            ("Role", "role"),
            ("Active", "is_active"),
            ("Staff", "is_staff"),
        ],
    ),
    (
        "Members",
        lambda: MemberProfile.objects.select_related("user").all().order_by("id"),
        [
            ("ID", "id"),
            ("First Name", lambda o: o.user.first_name),
            ("Last Name", lambda o: o.user.last_name),
            ("Phone", lambda o: str(o.user.phone_number)),
            ("Gender", "gender"),
            ("Age", "age"),
            ("Weight", "weight"),
            ("Height", "height"),
            ("Package", "package_type"),
            ("Package Price", lambda o: o.package_price),
            ("Start Date", lambda o: _fmt_dt(o.start_date)),
            ("End Date", lambda o: _fmt_dt(o.end_date)),
            ("Barcode", "barcode"),
            ("Is Renewal", "is_renewal"),
            ("Has Boxing", "has_boxing"),
            ("Boxing Expires", lambda o: _fmt_dt(o.boxing_expires_at)),
            ("Payment Recorded At", lambda o: _fmt_dt(o.payment_recorded_at)),
            ("Active", lambda o: o.user.is_active),
        ],
    ),
    (
        "Check-Ins",
        lambda: CheckIn.objects.select_related("member__user").all().order_by("-timestamp"),
        [
            ("ID", "id"),
            ("Member ID", lambda o: o.member.id),
            ("Member Name", lambda o: f"{o.member.user.first_name} {o.member.user.last_name}"),
            ("Timestamp", lambda o: _fmt_dt(o.timestamp)),
        ],
    ),
    (
        "Lockers",
        lambda: Locker.objects.all().order_by("locker_number"),
        [
            ("ID", "id"),
            ("Locker Number", "locker_number"),
            ("Status", "status"),
        ],
    ),
    (
        "Locker Assignments",
        lambda: LockerAssignment.objects.select_related("member__user", "locker").all().order_by("-assigned_at"),
        [
            ("ID", "id"),
            ("Member ID", lambda o: o.member.id),
            ("Member Name", lambda o: f"{o.member.user.first_name} {o.member.user.last_name}"),
            ("Locker Number", lambda o: o.locker.locker_number),
            ("Assigned At", lambda o: _fmt_dt(o.assigned_at)),
            ("Expires At", lambda o: _fmt_dt(o.expires_at)),
            ("Status", "status"),
        ],
    ),
    (
        "Pending Members",
        lambda: PendingMember.objects.select_related("recorded_by").all().order_by("-recorded_at"),
        [
            ("ID", "id"),
            ("First Name", "first_name"),
            ("Last Name", "last_name"),
            ("Phone", "phone_number"),
            ("Gender", "gender"),
            ("Age", "age"),
            ("Weight", "weight"),
            ("Height", "height"),
            ("Package", "package_type"),
            ("Package Price", lambda o: o.package_price),
            ("Pending Type", "pending_type"),
            ("Wants Boxing", "wants_boxing"),
            ("Activation Mode", "activation_mode"),
            ("Status", "status"),
            ("Recorded By", lambda o: _user_display(o, "recorded_by")),
            ("Recorded At", lambda o: _fmt_dt(o.recorded_at)),
            ("Activated At", lambda o: _fmt_dt(o.activated_at)),
        ],
    ),
    (
        "Pending History",
        lambda: PendingMemberHistory.objects.select_related("recorded_by").all().order_by("-activated_at"),
        [
            ("ID", "id"),
            ("First Name", "first_name"),
            ("Last Name", "last_name"),
            ("Phone", "phone_number"),
            ("Gender", "gender"),
            ("Age", "age"),
            ("Weight", "weight"),
            ("Height", "height"),
            ("Package", "package_type"),
            ("Pending Type", "pending_type"),
            ("Wants Boxing", "wants_boxing"),
            ("Activation Mode", "activation_mode"),
            ("Recorded By", lambda o: _user_display(o, "recorded_by")),
            ("Recorded At", lambda o: _fmt_dt(o.recorded_at)),
            ("Activated At", lambda o: _fmt_dt(o.activated_at)),
        ],
    ),
    (
        "Temporal Members",
        lambda: TemporalMember.objects.all().order_by("-paid_at"),
        [
            ("ID", "id"),
            ("Full Name", "full_name"),
            ("Age", "age"),
            ("Amount", "amount"),
            ("Paid At", lambda o: _fmt_dt(o.paid_at)),
        ],
    ),
    (
        "Diet Plans",
        lambda: TrainerDietplan.objects.all().order_by("id"),
        [
            ("ID", "id"),
            ("Title", "title"),
            ("Description", "description"),
            ("Items", lambda o: str(o.items)),
            ("Image URL", "image_url"),
        ],
    ),
]


def _fmt_dt(dt):
    """Format a datetime for display; return empty string if None."""
    if dt is None:
        return ""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _get_value(obj, accessor):
    """Resolve an accessor (string field name or callable) to a value."""
    if callable(accessor):
        return accessor(obj)
    return getattr(obj, accessor, "")


# ── Excel styling ────────────────────────────────────────────────────

HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
THIN_BORDER = Border(
    left=Side(style="thin", color="B4C6E7"),
    right=Side(style="thin", color="B4C6E7"),
    top=Side(style="thin", color="B4C6E7"),
    bottom=Side(style="thin", color="B4C6E7"),
)
ALT_ROW_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
DATA_FONT = Font(name="Calibri", size=10)


class Command(BaseCommand):
    help = "Export all database tables to Excel and/or CSV files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            type=str,
            choices=["excel", "csv", "both"],
            default="both",
            help="Export format: excel, csv, or both (default: both).",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Output directory (default: exports/ in the project root).",
        )

    def handle(self, *args, **options):
        fmt = options["format"]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        output_dir = options["output"] or os.path.join(os.getcwd(), "exports")
        os.makedirs(output_dir, exist_ok=True)

        if fmt in ("csv", "both"):
            csv_dir = os.path.join(output_dir, f"csv_{timestamp}")
            os.makedirs(csv_dir, exist_ok=True)
            self._export_csvs(csv_dir)
            self.stdout.write(self.style.SUCCESS(f"✅ CSV files saved to: {csv_dir}"))

        if fmt in ("excel", "both"):
            excel_path = os.path.join(output_dir, f"G2Gym_Export_{timestamp}.xlsx")
            self._export_excel(excel_path)
            self.stdout.write(self.style.SUCCESS(f"✅ Excel file saved to: {excel_path}"))

        self.stdout.write(self.style.SUCCESS("\n🎉 Export complete!"))

    # ── CSV export ───────────────────────────────────────────────────

    def _export_csvs(self, csv_dir):
        for sheet_name, qs_fn, columns in TABLE_DEFS:
            filename = sheet_name.lower().replace(" ", "_").replace("-", "_") + ".csv"
            filepath = os.path.join(csv_dir, filename)

            headers = [col[0] for col in columns]
            accessors = [col[1] for col in columns]

            rows_data = list(qs_fn())
            count = len(rows_data)

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for obj in rows_data:
                    row = [_get_value(obj, acc) for acc in accessors]
                    writer.writerow(row)

            self.stdout.write(f"  📄 {filename} — {count} rows")

    # ── Excel export ─────────────────────────────────────────────────

    def _export_excel(self, excel_path):
        wb = Workbook()
        # Remove the default empty sheet
        wb.remove(wb.active)

        for sheet_name, qs_fn, columns in TABLE_DEFS:
            ws = wb.create_sheet(title=sheet_name[:31])  # Excel sheet name max 31 chars
            headers = [col[0] for col in columns]
            accessors = [col[1] for col in columns]

            # Write header row
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = HEADER_FONT
                cell.fill = HEADER_FILL
                cell.alignment = HEADER_ALIGNMENT
                cell.border = THIN_BORDER

            # Write data rows
            rows_data = list(qs_fn())
            row_num = 2
            count = len(rows_data)
            for obj in rows_data:
                for col_idx, acc in enumerate(accessors, 1):
                    value = _get_value(obj, acc)
                    cell = ws.cell(row=row_num, column=col_idx, value=value)
                    cell.font = DATA_FONT
                    cell.border = THIN_BORDER
                    if row_num % 2 == 0:
                        cell.fill = ALT_ROW_FILL
                row_num += 1

            # Auto-fit column widths (approximate)
            for col_idx in range(1, len(headers) + 1):
                max_len = len(str(headers[col_idx - 1]))
                for row in range(2, min(row_num, 102)):  # sample first 100 rows
                    val = ws.cell(row=row, column=col_idx).value
                    if val is not None:
                        max_len = max(max_len, len(str(val)))
                adjusted_width = min(max_len + 3, 50)
                ws.column_dimensions[get_column_letter(col_idx)].width = adjusted_width

            # Freeze the header row
            ws.freeze_panes = "A2"

            self.stdout.write(f"  📊 {sheet_name} — {count} rows")

        wb.save(excel_path)
