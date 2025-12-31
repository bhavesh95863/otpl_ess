import frappe
import json
from frappe import _
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_otpl_leave_approval_list(start=0, page_length=10):
    """
    Get OTPL Leave Applications that need approval
    Filters: status='Pending' and approver is the session user
    """
    try:
        leave_list = frappe.get_all(
            "OTPL Leave",
            fields=[
                "name",
                "employee",
                "employee_name",
                "from_date",
                "to_date",
                "total_no_of_days",
                "half_day",
                "half_day_date",
                "alternate_mobile_no",
                "reason",
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters={"status": "Pending", "approver": frappe.session.user},
        )
        return gen_response(
            200, "Leave approval list retrieved successfully", leave_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read OTPL Leave")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_otpl_leave():
    """
    Approve OTPL Leave Application
    Accepts: name, approved_from_date, approved_to_date
    Sets status to 'Approved'
    """
    try:
        data = json.loads(frappe.request.get_data())
        leave_name = data.get("name")
        approved_from_date = data.get("approved_from_date")
        approved_to_date = data.get("approved_to_date")

        if not leave_name:
            return gen_response(500, "Leave name is required")

        if not approved_from_date or not approved_to_date:
            return gen_response(
                500, "Approved from date and approved to date are required"
            )

        # Check if leave exists
        if not frappe.db.exists("OTPL Leave", leave_name):
            return gen_response(500, "Leave application does not exist")

        # Get leave document
        leave_doc = frappe.get_doc("OTPL Leave", leave_name)

        # Verify that the current user is the approver
        if leave_doc.approver != frappe.session.user:
            return gen_response(
                500, "You are not authorized to approve this leave application"
            )

        # Check if already approved or rejected
        if leave_doc.status != "Pending":
            return gen_response(500, f"Leave application is already {leave_doc.status}")

        # Update leave document
        doc = frappe.get_doc("OTPL Leave", leave_name)
        doc.status = "Approved"
        doc.approved_from_date = approved_from_date
        doc.approved_to_date = approved_to_date
        doc.save(ignore_permissions=True)

        return gen_response(200, "Leave application approved successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to approve OTPL Leave")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def reject_otpl_leave():
    """
    Reject OTPL Leave Application
    Accepts: name
    Sets status to 'Rejected'
    """
    try:
        data = json.loads(frappe.request.get_data())
        leave_name = data.get("name")

        if not leave_name:
            return gen_response(500, "Leave name is required")

        # Check if leave exists
        if not frappe.db.exists("OTPL Leave", leave_name):
            return gen_response(500, "Leave application does not exist")

        # Get leave document
        leave_doc = frappe.get_doc("OTPL Leave", leave_name)

        # Verify that the current user is the approver
        if leave_doc.approver != frappe.session.user:
            return gen_response(
                500, "You are not authorized to reject this leave application"
            )

        # Check if already approved or rejected
        if leave_doc.status != "Pending":
            return gen_response(500, f"Leave application is already {leave_doc.status}")

        # Update leave document status to Rejected
        doc = frappe.get_doc("OTPL Leave", leave_name)
        doc.status = "Rejected"
        doc.save(ignore_permissions=True)

        return gen_response(200, "Leave application rejected successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to reject OTPL Leave")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_otpl_expense_approval_list(start=0, page_length=10):
    """
    Get OTPL Expense Applications that need approval
    Filters: approved_by_manager=0 and approval_manager is the session user
    """
    try:
        expense_list = frappe.get_all(
            "OTPL Expense",
            fields=[
                "name",
                "sent_by",
                "employee_name",
                "date_of_expense",
                "expense_type",
                "expense_claim_type",
                "amount",
                "details_of_expense",
                "purpose",
                "status",
                "business_line",
                "sales_order",
                "invoice_upload",
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters={"approved_by_manager": 0, "approval_manager": frappe.session.user},
        )
        return gen_response(
            200, "Expense approval list retrieved successfully", expense_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read OTPL Expense")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_otpl_expense():
    """
    Approve OTPL Expense
    Accepts: name, amount_approved
    Sets approved_by_manager to 1 (checked)
    """
    try:
        data = json.loads(frappe.request.get_data())
        expense_name = data.get("name")
        amount_approved = data.get("amount_approved")

        if not expense_name:
            return gen_response(500, "Expense name is required")

        if not amount_approved:
            return gen_response(500, "Amount approved is required")

        # Validate amount_approved is a number
        try:
            amount_approved = float(amount_approved)
            if amount_approved <= 0:
                return gen_response(500, "Amount approved must be greater than zero")
        except (ValueError, TypeError):
            return gen_response(500, "Invalid amount approved")

        # Check if expense exists
        if not frappe.db.exists("OTPL Expense", expense_name):
            return gen_response(500, "Expense application does not exist")

        # Get expense document
        expense_doc = frappe.get_doc("OTPL Expense", expense_name)

        # Verify that the current user is the approval manager
        if expense_doc.approval_manager != frappe.session.user:
            return gen_response(500, "You are not authorized to approve this expense")

        # Check if already approved
        if expense_doc.approved_by_manager == 1:
            return gen_response(500, "Expense is already approved")

        # Update expense document
        expense_doc.amount_approved = amount_approved
        expense_doc.approved_by_manager = 1
        expense_doc.save(ignore_permissions=True)

        return gen_response(200, "Expense approved successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to approve OTPL Expense")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_employee_checkin_approval_list(start=0, page_length=10, log_type=None):
    """
    Get Employee Checkin records that need approval
    Filters: approval_required=1, approved=0, manager is the session user
    Optional: log_type filter (IN/OUT)
    """
    try:
        filters = {
            "approval_required": 1,
            "approved": 0,
            "manager": frappe.session.user,
        }

        # Add log_type filter if provided
        if log_type:
            filters["log_type"] = log_type

        checkin_list = frappe.get_all(
            "Employee Checkin",
            fields=[
                "name",
                "employee",
                "employee_name",
                "log_type",
                "time",
                "requested_from",
                "reason",
                "location",
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
        )
        return gen_response(
            200, "Checkin approval list retrieved successfully", checkin_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read Employee Checkin")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_employee_checkin():
    """
    Approve Employee Checkin
    Sets approved to 1 (checked) and processes attendance
    """
    try:
        data = json.loads(frappe.request.get_data())
        checkin_name = data.get("name")

        if not checkin_name:
            return gen_response(500, "Checkin name is required")

        # Check if checkin exists
        if not frappe.db.exists("Employee Checkin", checkin_name):
            return gen_response(500, "Employee checkin does not exist")

        # Get checkin document
        checkin_doc = frappe.get_doc("Employee Checkin", checkin_name)

        # Verify that the current user is the manager
        if checkin_doc.manager != frappe.session.user:
            return gen_response(500, "You are not authorized to approve this check-in")

        # Check if already approved
        if checkin_doc.approved == 1:
            return gen_response(500, "Check-in is already approved")

        # Set approved field
        checkin_doc.approved = 1
        checkin_doc.save(ignore_permissions=True)

        # Process attendance for the employee on that day
        try:
            from employee_self_service.employee_self_service.utils.daily_attendance import (
                process_employee_attendance,
            )
            from frappe.utils import getdate

            attendance_date = getdate(checkin_doc.time)
            employee = checkin_doc.employee
            employee_location = frappe.db.get_value("Employee", employee, "location")

            # Check if attendance already exists
            existing_attendance = frappe.db.get_value(
                "Attendance",
                {
                    "employee": employee,
                    "attendance_date": attendance_date,
                    "docstatus": 1,
                },
                ["name", "leave_application"],
                as_dict=True,
            )

            if existing_attendance:
                if existing_attendance.leave_application:
                    # Leave-based attendance exists, don't process
                    return gen_response(
                        200,
                        "Check-in approved successfully. Attendance not processed due to existing leave application.",
                    )
                else:
                    # Cancel existing attendance to reprocess
                    att_doc = frappe.get_doc("Attendance", existing_attendance.name)
                    att_doc.cancel()
                    frappe.db.commit()

            # Process attendance for the day
            process_employee_attendance(employee, employee_location, attendance_date)

            return gen_response(
                200, "Check-in approved and attendance processed successfully"
            )

        except Exception as attendance_error:
            frappe.log_error(
                title=f"Approve Checkin - Attendance Processing Error: {checkin_doc.employee}",
                message=frappe.get_traceback(),
            )
            return gen_response(
                200,
                "Check-in approved successfully but attendance processing failed. Please check error logs.",
            )

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to approve Employee Checkin")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def reject_employee_checkin():
    """
    Reject Employee Checkin
    Accepts: name
    Deletes the checkin record
    """
    try:
        data = json.loads(frappe.request.get_data())
        checkin_name = data.get("name")

        if not checkin_name:
            return gen_response(500, "Checkin name is required")

        # Check if checkin exists
        if not frappe.db.exists("Employee Checkin", checkin_name):
            return gen_response(500, "Employee checkin does not exist")

        # Get checkin document
        checkin_doc = frappe.get_doc("Employee Checkin", checkin_name)

        # Verify that the current user is the manager
        if checkin_doc.manager != frappe.session.user:
            return gen_response(500, "You are not authorized to reject this check-in")

        # Check if already approved
        if checkin_doc.approved == 1:
            return gen_response(500, "Cannot reject an already approved check-in")

        # Delete the checkin record
        frappe.delete_doc("Employee Checkin", checkin_name, ignore_permissions=True)
        frappe.db.commit()

        return gen_response(200, "Check-in rejected successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to reject Employee Checkin")
    except Exception as e:
        return exception_handler(e)
