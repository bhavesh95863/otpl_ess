import frappe
from frappe.utils import add_months, get_last_day, nowdate, getdate

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
    
    if doc.advance_to_be_deducted > 0:
        doc.basic_salary = doc.advance_to_be_deducted / 2
    else:
        doc.basic_salary = 0
    if doc.is_team_leader:
        if doc.sales_order:
            doc.business_vertical = frappe.db.get_value("Sales Order", doc.sales_order, "business_line")
        if doc.external_order:
            external_so = frappe.get_doc("Sales Order Pull", doc.external_order)
            doc.external_so = external_so.sales_order
            doc.external_busoiness_vertical = external_so.business_line 