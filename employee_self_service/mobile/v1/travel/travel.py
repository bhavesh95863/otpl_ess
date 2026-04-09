import frappe
import json
from frappe import _
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
    get_employee_by_user,
)
from frappe.utils import today


@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_travel_request(**data):
    """Create or update a Travel Request for the current employee."""
    try:
        from frappe.handler import upload_file

        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name", "employee_name", "department"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exist")

        msg = ""
        if data.get("name"):
            travel_doc = frappe.get_doc("Travel Request", data.get("name"))
            if travel_doc.employee != emp_data.get("name"):
                return gen_response(500, "You are not authorized to update this travel request")
            if travel_doc.status != "Pending":
                return gen_response(500, "Only pending travel requests can be updated")
            msg = "Travel Request updated successfully"
        else:
            travel_doc = frappe.new_doc("Travel Request")
            travel_doc.employee = emp_data.get("name")
            msg = "Travel Request created successfully"

        travel_doc.update(data)
        travel_doc.save()

        if "file" in frappe.request.files:
            file = upload_file()
            file.attached_to_doctype = "Travel Request"
            file.attached_to_name = travel_doc.name
            file.save(ignore_permissions=True)
            frappe.db.set_value("Travel Request", travel_doc.name, "ticket", file.file_url)

        return gen_response(200, msg, {"name": travel_doc.name})
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to perform this action")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travel_request_list(start=0, page_length=10, filters=None):
    """List Travel Requests for the current employee."""
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exist")

        if filters:
            if isinstance(filters, str):
                filters = json.loads(filters)
        else:
            filters = []

        filters.append(["employee", "=", emp_data.get("name")])

        travel_list = frappe.get_all(
            "Travel Request",
            fields=[
                "name",
                "employee",
                "employee_name",
                "department",
                "date_of_departure",
                "date_of_arrival",
                "number_of_days",
                "purpose",
                "status",
                "report_to",
                "ticket",
                "remarks",
                "creation",
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
        )

        return gen_response(200, "Travel Request list retrieved successfully", travel_list)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read Travel Request")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travel_request_details(**data):
    """Get a single Travel Request by name."""
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exist")

        name = data.get("name")
        if not name:
            return gen_response(500, "Travel Request name is required")

        if not frappe.db.exists("Travel Request", name):
            return gen_response(500, "Travel Request does not exist")

        travel_doc = json.loads(frappe.get_doc("Travel Request", name).as_json())

        return gen_response(200, "Travel Request retrieved successfully", travel_doc)
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read Travel Request")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_travel_purpose_list():
    """Get the list of travel purposes."""
    try:
        purposes = [
            {"name": "Going on Leave"},
            {"name": "Going back to work"},
            {"name": "Going for official work"},
        ]
        return gen_response(200, "Travel purpose list retrieved successfully", purposes)
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def attach_travel_ticket(name=None, **data):
    """
    Attach a ticket image to a specific Travel Request.
    Args:
        name: Travel Request document name
        file: multipart file upload
    """
    try:
        from frappe.handler import upload_file

        name = name or data.get("name")
        if not name:
            return gen_response(400, "Travel Request name is required")

        if not frappe.db.exists("Travel Request", name):
            return gen_response(404, "Travel Request not found")

        travel_doc = frappe.get_doc("Travel Request", name)

        # Verify the travel request belongs to the current user's employee
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name"]
        )
        if not len(emp_data) >= 1:
            return gen_response(500, "Employee does not exist")

        if travel_doc.employee != emp_data.get("name"):
            return gen_response(403, "You don't have permission to upload image to this travel request")

        if "file" not in frappe.request.files:
            return gen_response(400, "No file provided")

        # Delete old ticket file if exists
        if travel_doc.ticket:
            old_file_doc = frappe.db.get_value("File", {"file_url": travel_doc.ticket}, "name")
            if old_file_doc:
                frappe.delete_doc("File", old_file_doc, ignore_permissions=True)

        # Upload new file
        file = upload_file()
        file.attached_to_doctype = "Travel Request"
        file.attached_to_name = travel_doc.name
        file.attached_to_field = "ticket"
        file.save(ignore_permissions=True)

        # Update travel request with file URL
        travel_doc.ticket = file.get("file_url")
        travel_doc.save(ignore_permissions=True)

        return gen_response(
            200,
            "Image uploaded successfully",
            {
                "name": travel_doc.name,
                "file_url": file.get("file_url"),
                "file_name": file.get("file_name"),
            },
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to perform this action")
    except Exception as e:
        return exception_handler(e)
