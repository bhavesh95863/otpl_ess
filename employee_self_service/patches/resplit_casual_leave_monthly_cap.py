import frappe
from collections import defaultdict
from frappe.utils import getdate, get_first_day, get_last_day, date_diff, add_days


# Detection / repair window (inclusive). Casual Leave applications whose
# from_date falls in this range are examined.
FROM_DATE = "2026-01-01"
TO_DATE = "2026-12-31"

MONTHLY_CL_CAP = 2.0
AUTO_STAMP_PREFIX = "Auto-created from OTPL Leave:"


def execute():
    """Re-split already-approved leaves that exceed the 2-Casual-Leave-days-per-
    calendar-month cap.

    The go-forward split (OTPLLeave._create_regular_leave_applications) now caps
    Casual Leave at 2 days per month and sends the rest to Leave Without Pay. This
    patch corrects historical data created before that rule: for every employee
    with more than 2 CL days in any month, it detaches the auto-created Leave
    Applications of the OTPL Leaves touching those months and recreates them, in
    approved-date order, through the same (now capped) logic.

    Idempotent: once a month is at/under the cap it is no longer flagged, so a
    second run is a no-op.
    """
    resplit_casual_leave_monthly_cap(dry_run=False, from_date=FROM_DATE, to_date=TO_DATE)


@frappe.whitelist()
def resplit_casual_leave_monthly_cap(dry_run=1, from_date=None, to_date=None):
    """Detect and (optionally) fix employees over the monthly Casual Leave cap.

    Pass dry_run=0 to actually rewrite the Leave Applications. In dry-run mode
    nothing is written — the affected employees/months and the OTPL Leaves that
    would be reprocessed are only reported.
    """
    dry_run = frappe.utils.cint(dry_run)
    from_date = getdate(from_date or FROM_DATE)
    to_date = getdate(to_date or TO_DATE)

    over_cap = _find_over_cap_months(from_date, to_date)   # {employee: set((y, m))}
    log = []

    if not over_cap:
        msg = "No employee is over the {0}-CL-days/month cap in {1}..{2}.".format(
            int(MONTHLY_CL_CAP), from_date, to_date
        )
        print(msg)
        return msg

    from employee_self_service.employee_self_service.utils.daily_attendance import (
        _detach_leave_applications,
    )

    fixed_employees = 0
    for employee, months in sorted(over_cap.items()):
        month_labels = ", ".join("{0}-{1:02d}".format(y, m) for (y, m) in sorted(months))
        leaves = _leaves_touching_months(employee, months)

        if dry_run:
            log.append("[DRY-RUN] {0}: over cap in {1}; would reprocess {2} leave(s): {3}".format(
                employee, month_labels, len(leaves), ", ".join(l.name for l in leaves)
            ))
            continue

        try:
            # 1. Detach ALL of this batch's Leave Applications first, so the
            #    monthly CL count starts clean before anything is recreated.
            for l in leaves:
                doc = frappe.get_doc("OTPL Leave", l.name)
                _detach_leave_applications(doc)
                _delete_attendance_in_range(doc.employee, l.approved_from_date, l.approved_to_date)

            # 2. Recreate in approved-date order, so the first 2 CL days of each
            #    month go to the earliest leaves and the rest become LWP.
            for l in leaves:
                doc = frappe.get_doc("OTPL Leave", l.name)
                doc._create_regular_leave_applications()

            frappe.db.commit()
            fixed_employees += 1
            log.append("FIXED {0}: reprocessed {1} leave(s) over months {2}".format(
                employee, len(leaves), month_labels
            ))
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Resplit CL monthly cap failed: {0}".format(employee),
                message=frappe.get_traceback(),
            )
            log.append("FAILED {0}: see Error Log (rolled back)".format(employee))

    header = "{0} employee(s) over the CL cap in {1}..{2}.{3}".format(
        len(over_cap), from_date, to_date,
        "" if dry_run else " Fixed {0}.".format(fixed_employees),
    )
    result = header + "\n" + "\n".join(log)
    print(result)
    return result


def _find_over_cap_months(from_date, to_date):
    """Return {employee: set((year, month))} for every calendar month in which an
    employee has more than MONTHLY_CL_CAP Casual Leave DAYS, counted from the
    auto-created Leave Applications. Cross-month applications contribute their days
    to each month they overlap; a half-day application counts 0.5.
    """
    rows = frappe.get_all(
        "Leave Application",
        filters={
            "leave_type": "Casual Leave",
            "docstatus": 1,
            "from_date": ["<=", to_date],
            "to_date": [">=", from_date],
            "description": ["like", AUTO_STAMP_PREFIX + "%"],
        },
        fields=["employee", "from_date", "to_date", "half_day", "half_day_date"],
    )

    per_month = defaultdict(lambda: defaultdict(float))   # employee -> (y,m) -> days
    for r in rows:
        s, e = getdate(r.from_date), getdate(r.to_date)
        d = s
        while d <= e:
            weight = 1.0
            if r.half_day and r.half_day_date and getdate(r.half_day_date) == d:
                weight = 0.5
            per_month[r.employee][(d.year, d.month)] += weight
            d = add_days(d, 1)

    out = {}
    for employee, months in per_month.items():
        flagged = {ym for ym, days in months.items() if days > MONTHLY_CL_CAP}
        if flagged:
            out[employee] = flagged
    return out


def _leaves_touching_months(employee, months):
    """Approved OTPL Leaves for ``employee`` that have at least one non-cancelled
    auto-created Leave Application overlapping any of the given (year, month)
    pairs, ordered by approved_from_date. These are reprocessed together so the
    monthly CL accounting is rebuilt consistently.
    """
    month_bounds = [(get_first_day(getdate("{0}-{1:02d}-01".format(y, m))),
                     get_last_day(getdate("{0}-{1:02d}-01".format(y, m)))) for (y, m) in months]

    # Only FULL-DAY leaves are re-split here. Half-day / short-leave records are
    # left untouched: half days are handled by the merge / no-Leave-Application
    # design, not by the CL/LWP split. Their CL days still count toward the cap
    # (via _casual_leave_days_in_month), so the full-day re-split accounts for them.
    leaves = frappe.get_all(
        "OTPL Leave",
        filters={
            "employee": employee,
            "status": "Approved",
            "half_day": 0,
            "short_leave": 0,
        },
        fields=["name", "approved_from_date", "approved_to_date"],
        order_by="approved_from_date asc",
    )

    selected = []
    for l in leaves:
        if not (l.approved_from_date and l.approved_to_date):
            continue
        lf, lt = getdate(l.approved_from_date), getdate(l.approved_to_date)
        if not any(lf <= mb_end and lt >= mb_start for (mb_start, mb_end) in month_bounds):
            continue
        # Only leaves that actually produced a Leave Application need rebuilding.
        if frappe.db.exists("Leave Application", {
            "description": "{0} {1}".format(AUTO_STAMP_PREFIX, l.name),
            "docstatus": ["<", 2],
        }):
            selected.append(l)
    return selected


def _delete_attendance_in_range(employee, start, end):
    """Cancel (if submitted) and delete every Attendance for ``employee`` in
    [start, end], so recreated Leave Applications regenerate it cleanly."""
    if not (start and end):
        return 0
    attendances = frappe.get_all(
        "Attendance",
        filters={"employee": employee, "attendance_date": ["between", [getdate(start), getdate(end)]]},
        fields=["name", "docstatus"],
    )
    for att in attendances:
        if att.docstatus == 1:
            att_doc = frappe.get_doc("Attendance", att.name)
            att_doc.flags.ignore_permissions = True
            att_doc.cancel()
        frappe.delete_doc("Attendance", att.name, force=True, ignore_permissions=True)
    return len(attendances)
