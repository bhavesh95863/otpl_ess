import frappe
import json
from frappe import _
# from frappe.utils import pretty_date, getdate, fmt_money
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
)



@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_approval_list(start=0, page_length=10):
    try:

        leave_list = frappe.get_all(
            "OTPL Leave",
            fields=["*"],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters={
                "approver": frappe.session.user
            },
        )
        return gen_response(200, "Expense List getting Successfully", leave_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read OTPL Expense")
    except Exception as e:
        return exception_handler(e)
    

@frappe.whitelist()
@ess_validate(methods=["POST"])
def update_leave_approval_status():
    try:
        data = json.loads(frappe.request.get_data())
        for leave in data.get("leave_list"):
            frappe.db.set_value(
                "OTPL Leave",
                leave.get("name"), 
                {
                    "status": data.get("status"),
                    "approved_from_date": leave.get("approved_from_date"),
                    "approved_to_date": leave.get("approved_to_date")
                }
            )
        frappe.db.commit()
        return gen_response(200, "Status updated Successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read OTPL Expense")
    except Exception as e:
        return exception_handler(e)
    

