import frappe
from frappe.utils import cint


def update_reports_to(employee_name, report_to, external,log_doc=None):
    """Update the reporting manager for an employee.

    Args:
        employee_name: Employee document name
        report_to: Name of the new reporting manager
        external: Whether the reporting manager is external (1) or internal (0)
    """
    if not report_to:
        return

    report_to_change = False
    employee_doc = frappe.get_doc("Employee", employee_name)

    if cint(external) == 1:
        if not employee_doc.external_report_to == report_to:
            employee_doc.external_report_to = report_to
            employee_doc.reports_to = None
            employee_doc.external_reporting_manager = 1
            report_to_change = True
    else:
        if not employee_doc.reports_to == report_to:
            employee_doc.reports_to = report_to
            employee_doc.external_report_to = None
            employee_doc.external_reporting_manager = 0
            report_to_change = True

    if report_to_change:
        employee_doc.save(ignore_permissions=True)
        if log_doc:
            log_doc.reports_to = report_to
            log_doc.reports_to_change = 1
            if cint(external) == 1:
                log_doc.reports_to_name = frappe.db.get_value("Employee Pull", report_to, "employee_name")
            else:
                log_doc.reports_to_name = frappe.db.get_value("Employee", report_to, "employee_name")

            log_doc.save(ignore_permissions=True)