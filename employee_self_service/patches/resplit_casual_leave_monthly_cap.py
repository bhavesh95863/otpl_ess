import frappe
from collections import defaultdict
from frappe.utils import getdate, get_first_day, get_last_day, date_diff, add_days, flt
from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on


# Detection / repair window (inclusive). Casual Leave applications whose
# from_date falls in this range are examined.
FROM_DATE = "2026-08-01"
TO_DATE = "2026-08-31"

# Optional: set to an Employee ID to restrict the patch to a single employee.
# Leave as None to process every employee over the cap.
EMPLOYEE = ""

MONTHLY_CL_CAP = 2.0
AUTO_STAMP_PREFIX = "Auto-created from OTPL Leave:"
CASUAL_LEAVE = "Casual Leave"
LWP = "Leave Without Pay"


def execute():
    """Re-split already-approved leaves so every calendar month lands on the
    intended shape: Casual Leave up to 2 days, then Leave Without Pay.

    The go-forward split (OTPLLeave._create_regular_leave_applications) caps Casual
    Leave at 2 days per calendar month and sends every further day to Leave Without
    Pay. This patch corrects historical data that does not match, in BOTH
    directions:

      * OVER cap  — more than 2 CL days in a month: the surplus must become LWP.
      * UNDER cap — LWP days in a month while the employee took fewer than 2 CL
        days AND still has annual Casual Leave balance: those days were written as
        LWP even though CL was available, so the first 2 days of the month must go
        back to CL.

    In both cases the auto-created Leave Applications of the OTPL Leaves touching
    those months are detached and recreated, in approved-date order, through the
    same (capped) logic — so the earliest days of each month take the 2 CL days and
    the rest become LWP.

    Idempotent: once a month reads exactly "CL up to the cap, then LWP" it is no
    longer flagged, so a second run is a no-op.
    """
    resplit_casual_leave_monthly_cap(
        dry_run=False, from_date=FROM_DATE, to_date=TO_DATE, employee=EMPLOYEE
    )


@frappe.whitelist()
def resplit_casual_leave_monthly_cap(dry_run=1, from_date=None, to_date=None, employee=None):
    """Detect and (optionally) fix months that do not match "CL up to the cap,
    then LWP" — both over-cap CL and LWP written while CL balance was available.

    Pass dry_run=0 to actually rewrite the Leave Applications. In dry-run mode
    nothing is written — the affected employees/months and the OTPL Leaves that
    would be reprocessed are only reported.

    Pass ``employee`` (an Employee ID, or a list / comma-separated string of them)
    to restrict the run to those employees only; omit it to scan everyone.
    """
    dry_run = frappe.utils.cint(dry_run)
    from_date = getdate(from_date or FROM_DATE)
    to_date = getdate(to_date or TO_DATE)
    employees = _normalize_employees(employee if employee is not None else EMPLOYEE)
    scope = " for {0}".format(", ".join(employees)) if employees else ""

    # {employee: {(y, m): reason}}
    flagged = _find_months_to_resplit(from_date, to_date, employees)
    log = []

    if not flagged:
        msg = ("Every month already reads CL up to {0} day(s) then LWP in {1}..{2}{3}"
               " — nothing to re-split.").format(int(MONTHLY_CL_CAP), from_date, to_date, scope)
        print(msg)
        return msg

    from employee_self_service.employee_self_service.utils.daily_attendance import (
        _detach_leave_applications,
    )

    fixed_employees = 0
    # Cancelling and re-submitting a Leave Application fires the employee's
    # leave-status email. Nobody should be mailed about a historical re-split, and
    # on a site whose outgoing mail is unusable the send raises mid-detach and
    # leaves the batch half-torn-down — so silence mail for the duration.
    _real_sendmail = frappe.sendmail
    frappe.sendmail = lambda *args, **kwargs: None
    try:
        for employee, months in sorted(flagged.items()):
            month_labels = ", ".join(
                "{0}-{1:02d} ({2})".format(y, m, months[(y, m)]) for (y, m) in sorted(months)
            )
            leaves = _leaves_touching_months(employee, months)

            if not leaves:
                log.append("SKIPPED {0}: {1}; no full-day OTPL Leave to reprocess".format(
                    employee, month_labels
                ))
                continue

            if dry_run:
                log.append("[DRY-RUN] {0}: {1}; would reprocess {2} leave(s): {3}".format(
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
                log.append("FIXED {0}: reprocessed {1} leave(s) for months {2}".format(
                    employee, len(leaves), month_labels
                ))
            except Exception:
                frappe.db.rollback()
                frappe.log_error(
                    title="Resplit CL monthly cap failed: {0}".format(employee),
                    message=frappe.get_traceback(),
                )
                log.append("FAILED {0}: see Error Log (rolled back)".format(employee))
    finally:
        frappe.sendmail = _real_sendmail

    header = "{0} employee(s) need a CL/LWP re-split in {1}..{2}{3}.{4}".format(
        len(flagged), from_date, to_date, scope,
        "" if dry_run else " Fixed {0}.".format(fixed_employees),
    )
    result = header + "\n" + "\n".join(log)
    print(result)
    return result


def _normalize_employees(employee):
    """Accept None / "HR-EMP-0001" / "a,b" / ["a", "b"] / a JSON list (whitelisted
    calls arrive as strings) and return a clean list of Employee IDs, or []."""
    if not employee:
        return []
    if isinstance(employee, str):
        employee = employee.strip()
        if employee.startswith("["):
            employee = frappe.parse_json(employee)
        else:
            employee = employee.split(",")
    return [str(e).strip() for e in employee if str(e).strip()]


def _find_months_to_resplit(from_date, to_date, employees=None):
    """Return {employee: {(year, month): reason}} for every calendar month whose
    auto-created Leave Applications do not read "Casual Leave up to
    MONTHLY_CL_CAP days, then Leave Without Pay".

    Two reasons are flagged:

      "over cap"  — more than MONTHLY_CL_CAP Casual Leave days in the month; the
                    surplus belongs in LWP.
      "LWP with CL balance"
                  — the month has full-day LWP days while fewer than
                    MONTHLY_CL_CAP CL days were taken AND the employee still has
                    annual Casual Leave balance; those days were written as LWP
                    even though Casual Leave was available.

    Cross-month applications contribute their days to each month they overlap; a
    half-day application counts 0.5. ``employees`` (a list of Employee IDs)
    restricts the scan to those employees.
    """
    filters = {
        "leave_type": ["in", [CASUAL_LEAVE, LWP]],
        "docstatus": 1,
        "from_date": ["<=", to_date],
        "to_date": [">=", from_date],
        "description": ["like", AUTO_STAMP_PREFIX + "%"],
    }
    if employees:
        filters["employee"] = ["in", employees]

    rows = frappe.get_all(
        "Leave Application",
        filters=filters,
        fields=["employee", "leave_type", "from_date", "to_date", "half_day",
                "half_day_date", "description"],
    )

    # employee -> (y, m) -> leave_type -> days
    per_month = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for r in rows:
        # Only LWP that came from a FULL-DAY leave can be re-split back into CL;
        # half-day / short-leave records are left alone by _leaves_touching_months,
        # so counting them here would flag a month that can never be fixed.
        if r.leave_type == LWP and not _is_full_day_source(r.description):
            continue
        d = getdate(r.from_date)
        last = getdate(r.to_date)
        while d <= last:
            weight = 1.0
            if r.half_day and r.half_day_date and getdate(r.half_day_date) == d:
                weight = 0.5
            per_month[r.employee][(d.year, d.month)][r.leave_type] += weight
            d = add_days(d, 1)

    out = {}
    for employee, months in per_month.items():
        flagged = {}
        for ym, by_type in months.items():
            cl_days = by_type.get(CASUAL_LEAVE, 0.0)
            lwp_days = by_type.get(LWP, 0.0)

            if cl_days > MONTHLY_CL_CAP:
                flagged[ym] = "over cap"
            elif lwp_days > 0 and cl_days < MONTHLY_CL_CAP and _has_cl_balance(employee, ym):
                flagged[ym] = "LWP with CL balance"
        if flagged:
            out[employee] = flagged
    return out


def _is_full_day_source(description):
    """True when an auto-created Leave Application's source OTPL Leave is a plain
    full-day leave — the only kind this patch re-splits."""
    leave = (description or "").replace(AUTO_STAMP_PREFIX, "", 1).strip()
    if not leave:
        return False
    row = frappe.db.get_value("OTPL Leave", leave, ["half_day", "short_leave"], as_dict=True)
    return bool(row) and not row.half_day and not row.short_leave


def _has_cl_balance(employee, ym):
    """True when the employee still has annual Casual Leave balance in ``ym``'s
    allocation period. Without balance, LWP is the correct outcome and the month
    must not be flagged — otherwise every run would reprocess it forever.

    The already-booked CL of this month counts as available, since re-splitting
    cancels those applications before recreating them. A full day is required:
    only full-day leaves are re-split, so a balance under 1 cannot buy a day and a
    month resting on it would otherwise be flagged on every run.
    """
    month_start = get_first_day(getdate("{0}-{1:02d}-01".format(ym[0], ym[1])))
    try:
        balance = flt(get_leave_balance_on(
            employee=employee,
            leave_type=CASUAL_LEAVE,
            date=month_start,
            consider_all_leaves_in_the_allocation_period=True,
        ) or 0)
    except Exception:
        # No allocation covering the month -> no balance -> LWP is correct.
        return False
    return balance >= 1.0


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
