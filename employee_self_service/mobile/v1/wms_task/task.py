import frappe
import json
from frappe import _
# from frappe.utils import pretty_date, getdate, fmt_money
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
    get_employee_by_user,
)
from frappe.utils import get_datetime

@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_task(**data):
    try:
        emp_data = get_employee_by_user(
        frappe.session.user, fields=["name", "image", "department","company"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exists")
        msg = ""
        if data.get("name"):
            wms_task_doc = frappe.get_doc("WMS Task",data.get("name"))
            msg = "Task updated successfully"
        else:
            wms_task_doc = frappe.new_doc("WMS Task")
            msg = "Task create successfully"
        wms_task_doc.update(data)
        wms_task_doc.save()
        return gen_response(200, msg)
    except frappe.PermissionError:
            return gen_response(500, "Not permitted to perform this action")
    except Exception as e:
            return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_list(start=0, page_length=10, filters=None):
    try:
        timesheet_list = frappe.get_list(
            "WMS Task",
            fields=[
                "name",
                "task_title",
                "date_of_issue",
                "due_date",
                "status",
                "source",
                "mark_incomplete",
                "date_of_completion",
                "completed",
                "date_extend_request",
                "reason",
                "details",
                "assign_by",
                "assign_to"
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
        )
        return gen_response(200, "Task List getting Successfully", timesheet_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted read WMS Task")
    except Exception as e:
        return exception_handler(e)
    
@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_task_details(**data):
    try:
        wms_task_doc = json.loads(frappe.get_doc("WMS Task",data.get("name")).as_json())
        wms_task_doc["edit"] = False
        wms_task_doc["complete_action"] = False
        wms_task_doc["extend_action"] = False
        wms_task_doc["extend_workflow_action"] = False
        wms_task_doc["reopen_action"] = False
        if "WMS Admin" in frappe.get_roles(frappe.session.user) or "System Manager" in frappe.get_roles(frappe.session.user):
            wms_task_doc["edit"] = True
        if not wms_task_doc.get("status") == "Extend Required" and wms_task_doc.get("assign_to") == frappe.session.user:
            if wms_task_doc.get("status") in ['Not Yet Due',' Due Today',' Without Due Date','Overdue']:
                wms_task_doc["complete_action"] = True
            if not wms_task_doc.get("status") == "Overdue":
                wms_task_doc["extend_action"] = True
        if wms_task_doc.get("status") == "Extend Required" and ("WMS Admin" in frappe.get_roles(frappe.session.user) or "System Manager" in frappe.get_roles(frappe.session.user)):
            wms_task_doc["extend_workflow_action"] = True
        if wms_task_doc.get("status") == "Late" or wms_task_doc.get("status") == "Ontime":
            if "WMS Admin" in frappe.get_roles(frappe.session.user) or "System Manager" in frappe.get_roles(frappe.session.user):
                wms_task_doc["reopen_action"] = True
        return gen_response(200, "WMS Task get successfully", wms_task_doc)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for read WMS Task")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["POST"])
def mark_task_complete(**data):
    try:
        wms_task_doc = frappe.get_doc("WMS Task",data.get("name"))
        wms_task_doc.mark_complete()
        return gen_response(200,"Task marked as completed successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for write WMS Task")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def extend_date_request(**data):
    try:
        from wms.wms.doctype.wms_task.wms_task import extend_date_request
        extend_date_request(task_id=data.get("name"),date=data.get("extend_date"),reason=data.get("reason"))
        return gen_response(200,"Extend request successfully added")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for write WMS Task")
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_extend_request(**data):
    try:
        wms_task_doc = frappe.get_doc("WMS Task",data.get("name"))
        if not wms_task_doc.get("status") == "Extend Required":
            return gen_response(500,"Invalid action")
        wms_task_doc.approve_extend_request()
        return gen_response(200,"Request approved successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for write WMS Task")
    except Exception as e:
        return exception_handler(e)    


@frappe.whitelist()
@ess_validate(methods=["POST"])
def reject_extend_request(**data):
    try:
        wms_task_doc = frappe.get_doc("WMS Task",data.get("name"))
        if not wms_task_doc.get("status") == "Extend Required":
            return gen_response(500,"Invalid action")
        wms_task_doc.reject_extend_request()
        return gen_response(200,"Request rejected successfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for write WMS Task")
    except Exception as e:
        return exception_handler(e)    

@frappe.whitelist()
@ess_validate(methods=["POST"])
def reopen_task(**data):
    try:
        wms_task_doc = frappe.get_doc("WMS Task",data.get("name"))
        wms_task_doc.mark_uncomplete()
        return gen_response(200,"Task reopened succesfully")
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for write WMS Task")
    except Exception as e:
        return exception_handler(e)  

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_user_list(start=0, page_length=10):
    try:
        users = frappe.get_list("User",filters={"enabled":1},fields=["name","full_name","email","user_image"],start=start,page_length=page_length)
        return gen_response(200,"User list get successfully",users)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted for read user")
    except Exception as e:
        return exception_handler(e)