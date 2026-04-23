import frappe
import json
import requests
from frappe import _
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
    business_vertical = doc.business_vertical or doc.external_business_vertical
    if doc.is_team_leader and business_vertical:
        business_line_doc = frappe.get_doc("Business Line", business_vertical)
        if business_line_doc.reporting_manager:
            doc.reports_to = business_line_doc.reporting_manager
            doc.external_report_to = None
            doc.external_reporting_manager = 0
        if business_line_doc.external_reporting_manager:
            doc.external_report_to = business_line_doc.external_reporting_manager
            doc.external_reporting_manager = 1
            doc.reports_to = None
    if doc.temp_tl == 1:
        doc.is_team_leader = 1
    

def assign_team_leader_role_on_temp_tl(doc, method):
    """Assign or remove 'TEAM LEADER' role on the linked user based on temp_tl."""
    if not doc.user_id:
        return

    # if doc.temp_tl:
    #     # Add TEAM LEADER role if not already present
    #     user = frappe.get_doc("User", doc.user_id)
    #     user_roles = [r.role for r in user.roles]
    #     if "TEAM LEADER" not in user_roles:
    #         user.append("roles", {"role": "TEAM LEADER"})
    #         frappe.flags.syncing_employee_from_user_roles = True
    #         try:
    #             user.save(ignore_permissions=True)
    #         finally:
    #             frappe.flags.syncing_employee_from_user_roles = False

def remove_team_leader_role(user_id):
    """Remove the 'TEAM LEADER' role from the given user."""
    user = frappe.get_doc("User", user_id)
    role_to_remove = None
    for r in user.roles:
        if r.role == "TEAM LEADER":
            role_to_remove = r
            break
    if role_to_remove:
        user.roles.remove(role_to_remove)
        frappe.flags.syncing_employee_from_user_roles = True
        try:
            user.save(ignore_permissions=True)
        finally:
            frappe.flags.syncing_employee_from_user_roles = False


@frappe.whitelist()
def get_ess_information(employee):
    """
    Returns ESS information for the given employee:
    - self: current employee's check-in and device registration details
    - reports_to: details of the employee's internal manager
    - external_reports_to: details of the employee's external manager (from remote ERP)
    - reportees: list of internal employees who report to this employee
    - external_reportees: list of employees from remote ERP who report to this employee
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

    def call_remote_erp(api_method, params):
        """Call a remote ERP API using ERP Sync Settings. Tries all enabled settings."""
        sync_settings = frappe.get_all(
            "ERP Sync Settings",
            filters={"enabled": 1},
            fields=["name"]
        )
        for s in sync_settings:
            try:
                settings = frappe.get_doc("ERP Sync Settings", s.name)
                url = "{0}/api/method/employee_self_service.employee_self_service.utils.erp_sync.{1}".format(
                    settings.erp_url, api_method
                )
                headers = {
                    "Authorization": "token {0}:{1}".format(
                        settings.get_password("api_key"),
                        settings.get_password("api_secret")
                    ),
                    "Content-Type": "application/json"
                }
                response = requests.get(url, params=params, headers=headers, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    msg = data.get("message", {})
                    if msg.get("success"):
                        return msg.get("data", {})
            except Exception:
                frappe.log_error(
                    message=frappe.get_traceback(),
                    title="ESS Info: Error calling remote ERP {0}".format(s.name)
                )
        return None

    # Self
    self_info = build_employee_row(employee)

    # Get external reporting fields
    emp_data = frappe.db.get_value(
        "Employee", employee,
        ["reports_to", "external_reporting_manager", "external_report_to"],
        as_dict=True
    )

    # Internal Manager
    manager_info = build_employee_row(emp_data.reports_to) if emp_data and emp_data.reports_to else None

    # External Manager (fetch from remote ERP via ERP Sync Settings)
    external_manager_info = None
    if emp_data and emp_data.external_reporting_manager and emp_data.external_report_to:
        remote_data = call_remote_erp(
            "get_external_employee_ess_details",
            {"employee": emp_data.external_report_to}
        )
        if remote_data and emp_data.external_report_to in remote_data:
            ext = remote_data[emp_data.external_report_to]
            external_manager_info = {
                "employee": ext.get("employee", ""),
                "employee_name": ext.get("employee_name", ""),
                "designation": ext.get("designation", ""),
                "checkin_time": ext.get("checkin_time", ""),
                "device_registered": ext.get("device_registered", "N/A"),
                "is_external": True,
            }
        else:
            # Fallback to local Employee Pull data
            pull_data = frappe.db.get_value(
                "Employee Pull", {"employee": emp_data.external_report_to},
                ["employee", "employee_name", "company"],
                as_dict=True
            )
            if pull_data:
                external_manager_info = {
                    "employee": pull_data.employee or "",
                    "employee_name": pull_data.employee_name or "",
                    "designation": pull_data.company or "External",
                    "checkin_time": "",
                    "device_registered": "N/A",
                    "is_external": True,
                }

    # Internal Reportees (employees whose reports_to = this employee)
    reportees_list = frappe.db.get_all(
        "Employee",
        filters={"reports_to": employee, "status": "Active"},
        fields=["name"],
        order_by="employee_name asc"
    )
    reportees = [r for r in (build_employee_row(e.name) for e in reportees_list) if r]

    # External Reportees - employees on remote ERP who report to this employee
    external_reportees = []
    remote_reportees = call_remote_erp(
        "get_external_reportees",
        {"employee": employee}
    )
    if remote_reportees:
        for rep in remote_reportees:
            external_reportees.append({
                "employee": rep.get("employee", ""),
                "employee_name": rep.get("employee_name", ""),
                "designation": rep.get("designation", ""),
                "checkin_time": rep.get("checkin_time", ""),
                "device_registered": rep.get("device_registered", "N/A"),
                "is_external": True,
                "is_external_reportee": True,
            })

    # Also include local employees with external_report_to = this employee
    local_ext_reportees_list = frappe.db.get_all(
        "Employee",
        filters={"external_report_to": employee, "status": "Active"},
        fields=["name"],
        order_by="employee_name asc"
    )
    for e in local_ext_reportees_list:
        row = build_employee_row(e.name)
        if row:
            row["is_external_reportee"] = True
            external_reportees.append(row)

    return {
        "self": self_info,
        "reports_to": manager_info,
        "external_reports_to": external_manager_info,
        "reportees": reportees,
        "external_reportees": external_reportees,
    }


@frappe.whitelist()
def mark_travelling(employee, date_of_departure, date_of_arrival, purpose, ticket=None, remarks=None):
    """Create an auto-approved Travel Request for the employee.

    The Travel Request's own lifecycle hooks (`update_employee_travel_status`)
    will mark the employee as travelling based on the dates and purpose.
    """
    if not frappe.db.exists("Employee", employee):
        frappe.throw(_("Employee not found"))

    travel_request = frappe.get_doc({
        "doctype": "Travel Request",
        "employee": employee,
        "date_of_departure": date_of_departure,
        "date_of_arrival": date_of_arrival,
        "purpose": purpose,
        "ticket": ticket or None,
        "remarks": remarks or None,
        "status": "Approved",
    })
    travel_request.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"status": "success", "name": travel_request.name}