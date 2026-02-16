import frappe
from frappe import _
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
    get_employee_by_user
)
from frappe.utils import today
from frappe.handler import upload_file

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_work_progress_list(start=0, page_length=20, filters=None):
    try:
        if isinstance(filters, str):
            filters = frappe.parse_json(filters)

        if not filters:
            filters = {}
        employee_details = get_employee_by_user(
            frappe.session.user,
            fields=["name","sales_order","business_vertical"]
        )
        roles = frappe.get_roles(frappe.session.user)
        if not "WPE Admin" in roles or not "WPE Manager" in roles or not "WPE User" in roles:
            return gen_response(200, "Work Progress List fetched successfully", [])
        if "WPE Admin" in roles:
            pass
        elif "WPE Manager" in roles:
            filters["business_vertical"] = employee_details.business_vertical
        elif "WPE User" in roles:
            filters["order_no"] = employee_details.sales_order
            filters["business_vertical"] = employee_details.business_vertical

        work_progress_list = frappe.get_all(
            "Work Progress Entry",
            filters=filters,
            fields=["*"],
            start=int(start),
            page_length=int(page_length),
            order_by="creation desc"
        )

        return gen_response(200, "Work Progress List fetched successfully", work_progress_list)

    except frappe.PermissionError:
        return gen_response(403, "Not permitted to read Work Progress Entry")

    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_order_item_list():
    try:
        session_user = frappe.session.user

        employee = get_employee_by_user(
            session_user,
            fields=["name", "sales_order"]
        )

        if not employee or not employee.name:
            frappe.throw(_("Employee not found"))

        if not employee.sales_order:
            frappe.throw(_("Order No is not defined in employee"))

        items = get_sales_order_items(employee.sales_order)

        return gen_response(
            200,
            "Item list fetched successfully",
            items
        )

    except frappe.PermissionError:
        return gen_response(403, "Not permitted to read Order")

    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_work_progress_entry(**data):
    try:
        session_user = frappe.session.user

        item = data.get("item")
        qty = data.get("qty") or 0

        if not item:
            frappe.throw(_("Item is required"))

        employee = get_employee_by_user(
            session_user,
            fields=["name", "business_vertical", "sales_order"]
        )

        if not employee or not employee.name:
            frappe.throw(_("Employee not found"))

        if not employee.sales_order:
            frappe.throw(_("Order No is not defined in employee"))

        item_rate = get_sales_order_item_rate(
            employee.sales_order,
            item
        )

        doc = frappe.get_doc({
            "doctype": "Work Progress Entry",
            "employee": employee.name,
            "business_vertical": employee.business_vertical,
            "order_no": employee.sales_order,
            "item": item,
            "rate_of_item_in_sales_order": item_rate,
            "no_of_welds": qty,
            "select_date":"",
            "date": today()
        })
        doc.auto_date_select()
        doc.insert()

        return gen_response(
            200,
            "Work Progress Entry created successfully",
            {"name": doc.name}
        )

    except frappe.PermissionError:
        return gen_response(403, "You are not permitted to create Work Progress Entry")

    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["POST"])
def upload_register_image(work_progress_entry_name):
    """
    Attach an image to an existing Work Progress Entry document
    Args:
        work_progress_entry_name: Work Progress Entry document ID
    """
    try:
        # Validate work progress entry document exists
        if not frappe.db.exists("Work Progress Entry", work_progress_entry_name):
            return gen_response(404, "Work Progress Entry not found")

        # Get the work progress entry document
        work_progress_entry_doc = frappe.get_doc("Work Progress Entry", work_progress_entry_name)

        # Check if file is in request
        if "file" not in frappe.request.files:
            return gen_response(400, "No file provided")

        # Delete old image if exists
        if work_progress_entry_doc.register_image:
            old_file_url = work_progress_entry_doc.register_image
            file_doc = frappe.db.get_value("File", {"file_url": old_file_url}, "name")
            if file_doc:
                frappe.delete_doc("File", file_doc, ignore_permissions=True)

        # Upload new file
        file = upload_file()
        file.attached_to_doctype = "Work Progress Entry"
        file.attached_to_name = work_progress_entry_doc.name
        file.attached_to_field = "register_image"
        file.save(ignore_permissions=True)

        # Update work progress entry document with image URL
        work_progress_entry_doc.register_image = file.get("file_url")
        work_progress_entry_doc.save(ignore_permissions=True)

        return gen_response(
            200,
            "Image attached successfully",
            {
                "work_progress_entry_id": work_progress_entry_doc.name,
                "file_url": file.get("file_url"),
                "file_name": file.get("file_name")
            }
        )
    except Exception as e:
        return exception_handler(e)


def get_sales_order_items(order_no):
    items = []
    order_doc = frappe.get_doc("Sales Order",order_no)
    for row in order_doc.items:
        items.append(row.item_code)
    return items


def get_sales_order_item_rate(order_no, item):
    rate = frappe.get_value(
        "Sales Order Item",
        {"parent": order_no, "item_code": item},
        "rate"
    )

    return rate or 0