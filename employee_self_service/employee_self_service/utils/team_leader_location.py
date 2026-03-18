import frappe
from frappe.utils import today
from employee_self_service.employee_self_service.utils.otpl_attendance import sync_leader_location_to_remote

def after_team_leader_location_update_insert(doc, method):
    """After insert of Team Leader Location Update Log,
    update location_update field on the first IN Employee Checkin of the day for that employee."""
    location = doc.location
    employee = doc.employee

    if not location or not employee:
        return

    # Find the first IN checkin of the day for this employee
    first_checkin = frappe.db.sql("""
        SELECT name
        FROM `tabEmployee Checkin`
        WHERE employee = %s
        AND log_type = 'IN'
        AND time >= %s
        ORDER BY time ASC
        LIMIT 1
    """, (employee, today()), as_dict=1)

    if first_checkin:
        frappe.db.set_value(
            "Employee Checkin",
            first_checkin[0].name,
            "location_update",
            location,
            update_modified=False
        )
    sync_leader_location_to_remote(doc)
