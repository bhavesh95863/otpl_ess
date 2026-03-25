import frappe
from frappe.utils import getdate, now_datetime


@frappe.whitelist(allow_guest=True)
def locations(date=None):
    """Return employee check-in locations and active employee data.

    Accessible at: /api/method/employee_self_service.api.locations?date=2026-03-21

    Returns:
        records       – today's check-in records with location info
        total_active_employees – count of active employees
        employee_list – all active employees (for search dropdown)
    """
    if not date:
        date = frappe.utils.today()
    else:
        # Sanitise input
        date = str(getdate(date))

    # ── Fetch check-in records with location for the given date ──
    checkins = frappe.db.sql("""
        SELECT
            ec.employee,
            ec.employee_name,
            ec.time,
            ec.location,
            ec.address,
            ec.business_vertical,
            ec.sales_order,
            ec.log_type,
            emp.company
        FROM `tabEmployee Checkin` ec
        LEFT JOIN `tabEmployee` emp ON emp.name = ec.employee
        WHERE
            ec.location IS NOT NULL
            AND ec.location != ''
            AND DATE(ec.time) = %(date)s
            AND IFNULL(ec.rejected, 0) = 0
        ORDER BY ec.time DESC
    """, {"date": date}, as_dict=True)

    records = []
    for row in checkins:
        records.append({
            "employee": row.employee or "",
            "employee_name": row.employee_name or "",
            "location": row.location or "",
            "time": frappe.utils.format_datetime(row.time, "hh:mm a") if row.time else "",
            "company": row.company or "",
            "address": row.address or "",
            "business_vertical": row.business_vertical or "",
            "sales_order": row.sales_order or "",
            "log_type": row.log_type or "",
        })

    # ── Active employee count + list ─────────────────────────────
    employees = frappe.db.sql("""
        SELECT
            name AS employee,
            employee_name,
            company
        FROM `tabEmployee`
        WHERE status = 'Active'
        ORDER BY employee_name
    """, as_dict=True)

    employee_list = [
        {
            "employee": emp.employee,
            "employee_name": emp.employee_name or "",
            "company": emp.company or "",
        }
        for emp in employees
    ]

    # ── NTLE count for today ───────────────────────────────────
    ntle_count = frappe.db.sql("""
        SELECT COUNT(*) AS cnt
        FROM `tabNo Team Leader Error`
        WHERE DATE(datetime) = %(date)s
    """, {"date": date}, as_dict=True)[0].cnt or 0

    return {
        "records": records,
        "total_active_employees": len(employee_list),
        "employee_list": employee_list,
        "ntle_count": ntle_count,
    }
