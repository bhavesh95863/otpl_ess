import frappe
from frappe.utils import today, getdate
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    get_employee_by_user,
    exception_handler,
)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_people_on_leave(date=None):
    """
    Return the list of employees who are on approved leave on the given date.
    Accessible to all employees except those with Location = Site.

    Query params:
        date (str, optional): Date in YYYY-MM-DD format. Defaults to today.
    """
    try:
        emp_data = get_employee_by_user(
            frappe.session.user,
            fields=["name", "location"],
        )
        if not emp_data:
            return gen_response(500, "Employee does not exist!")

        if emp_data.get("location") == "Site":
            return gen_response(403, "You are not authorized to view this information.")

        check_date = getdate(date) if date else getdate(today())

        employees_on_leave = frappe.db.sql(
            """
            SELECT
                la.employee,
                la.employee_name
            FROM `tabLeave Application` la
            WHERE
                la.status = 'Approved'
                AND la.docstatus = 1
                AND la.from_date <= %(date)s
                AND la.to_date >= %(date)s
            ORDER BY la.employee_name ASC
            """,
            {"date": check_date},
            as_dict=True,
        )

        return gen_response(
            200,
            "People on leave fetched successfully",
            employees_on_leave,
        )
    except Exception as e:
        return exception_handler(e)
