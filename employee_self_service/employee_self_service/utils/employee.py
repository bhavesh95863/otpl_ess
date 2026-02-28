import frappe
from frappe.utils import add_months, get_last_day, nowdate, getdate, get_datetime, format_datetime,flt

def validate_employee(doc, method):
    """
    Validate and update basic_salary based on advance_to_be_deducted.
    Sets basic_salary = advance_to_be_deducted / 2 if advance_to_be_deducted > 0, else sets it to 0.
    """
    if doc.status == "Left":
        if doc.location == "Site":
            if not doc.relieving_date:
                doc.relieving_date = get_last_day(add_months(nowdate(), 1))
        else:
            if not doc.relieving_date:
                doc.relieving_date = getdate(add_months(nowdate(), 1)).replace(day=10)

    if flt(doc.advance_to_be_deducted) > 0:
        doc.basic_salary = doc.advance_to_be_deducted / 2
    else:
        doc.basic_salary = 0
    if doc.is_team_leader or doc.show_sales_order == 1:
        if doc.sales_order and not doc.external_sales_order:
            doc.business_vertical = frappe.db.get_value("Sales Order", doc.sales_order, "business_line")
        elif doc.external_order and doc.external_sales_order:
            external_so = frappe.get_doc("Sales Order Pull", doc.external_order)
            doc.external_so = external_so.sales_order
            doc.external_busoiness_vertical = external_so.business_line
            doc.business_vertical = ""
            doc.sales_order = ""
    if not doc.location == "Site" and not doc.sales_order:
        frappe.msgprint("Please ensure that you have selected the correct Business Vertical for this user and save again")

@frappe.whitelist()
def get_ess_information(employee):
    """
    Returns ESS information for the given employee:
    - self: current employee's check-in and device registration details
    - reports_to: details of the employee this person reports to
    - reportees: list of employees who report to this employee, with their details
    """
    today = nowdate()

    def get_checkin_time(emp):
        """Return the time of the first IN check-in of today for the given employee."""
        result = frappe.db.sql(
            """
            SELECT time FROM `tabEmployee Checkin`
            WHERE employee = %s
              AND log_type = 'IN'
              AND DATE(time) = %s
            ORDER BY time ASC
            LIMIT 1
            """,
            (emp, today),
            as_dict=True
        )
        if result and result[0].get("time"):
            return format_datetime(result[0]["time"], "HH:mm:ss")
        return ""

    def get_device_registered(emp):
        """Return Yes/No based on whether this employee has a registered device."""
        return "Yes" if frappe.db.exists("Employee Device Registration", {"employee": emp}) else "No"

    def build_employee_row(emp):
        if not emp:
            return None
        data = frappe.db.get_value(
            "Employee", emp,
            ["employee_name", "designation", "reports_to"],
            as_dict=True
        )
        if not data:
            return None
        return {
            "employee": emp,
            "employee_name": data.employee_name or "",
            "designation": data.designation or "",
            "checkin_time": get_checkin_time(emp),
            "device_registered": get_device_registered(emp),
        }

    # Self
    self_info = build_employee_row(employee)

    # Manager (reports_to of the current employee)
    reports_to_emp = frappe.db.get_value("Employee", employee, "reports_to")
    manager_info = build_employee_row(reports_to_emp) if reports_to_emp else None

    # Reportees (employees whose reports_to = this employee)
    reportees_list = frappe.db.get_all(
        "Employee",
        filters={"reports_to": employee, "status": "Active"},
        fields=["name"],
        order_by="employee_name asc"
    )
    reportees = [r for r in (build_employee_row(e.name) for e in reportees_list) if r]

    return {
        "self": self_info,
        "reports_to": manager_info,
        "reportees": reportees,
    }        