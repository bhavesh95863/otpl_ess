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
    Get OTPL Leave and Leave Pull Applications that need approval
    Filters: status='Pending' and approver is the session user (for OTPL Leave)
             status!='Approved' and source_erp is set (for Leave Pull)
    """
    try:
        # Get OTPL Leave records
        otpl_leave_list = frappe.get_all(
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
                "status",
                "modified",
            ],
            filters={"status": "Pending", "approver": frappe.session.user},
        )

        # Get Leave Pull records that need approval
        leave_pull_list = frappe.get_all(
            "Leave Pull",
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
                "status",
                "modified",
            ],
            filters=[
                ["source_erp", "is", "set"],
                ["status", "!=", "Approved"],
                ["approver_user", "=", frappe.session.user]
            ],
        )

        # Combine both lists
        combined_list = otpl_leave_list + leave_pull_list

        # Sort by modified date descending
        combined_list.sort(key=lambda x: x.get("modified"), reverse=True)

        # Apply pagination
        start = int(start)
        page_length = int(page_length)
        paginated_list = combined_list[start:start + page_length]

        # Clean up response - remove internal fields
        for item in paginated_list:
            item.pop("modified", None)

        return gen_response(
            200, "Leave approval list retrieved successfully", paginated_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read leave records")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_otpl_leave():
    """
    Approve OTPL Leave or Leave Pull Application (auto-detects doctype)
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

        # Auto-detect if it's a Leave Pull or OTPL Leave
        is_leave_pull = frappe.db.exists("Leave Pull", leave_name)
        is_otpl_leave = frappe.db.exists("OTPL Leave", leave_name)

        if not is_leave_pull and not is_otpl_leave:
            return gen_response(500, "Leave application does not exist")

        # Handle Leave Pull records
        if is_leave_pull:
            leave_doc = frappe.get_doc("Leave Pull", leave_name)

            # Check if already approved
            if leave_doc.status == "Approved":
                return gen_response(500, "Leave application is already approved")

            # Calculate approved days
            from frappe.utils import date_diff
            approved_days = date_diff(approved_to_date, approved_from_date) + 1

            # Update Leave Pull
            leave_doc.status = "Approved"
            leave_doc.approved_from_date = approved_from_date
            leave_doc.approved_to_date = approved_to_date
            leave_doc.total_no_of_approved_days = approved_days
            leave_doc.save(ignore_permissions=True)

            return gen_response(200, "Leave application approved successfully")

        # Handle OTPL Leave records
        else:
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
            leave_doc.status = "Approved"
            leave_doc.approved_from_date = approved_from_date
            leave_doc.approved_to_date = approved_to_date
            leave_doc.save(ignore_permissions=True)

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
    Get OTPL Expense and Expense Pull Applications that need approval
    Filters: approved_by_manager=0 and approval_manager is the session user (for OTPL Expense)
             approved_by_manager=0 and source_erp is set (for Expense Pull)
    """
    try:
        # Get OTPL Expense records
        otpl_expense_list = frappe.get_all(
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
                "modified",
            ],
            filters={"approved_by_manager": 0, "approval_manager": frappe.session.user},
        )

        # Get Expense Pull records that need approval
        expense_pull_list = frappe.get_all(
            "Expense Pull",
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
                "source_erp",
                "modified",
            ],
            filters=[
                ["source_erp", "is", "set"],
                ["approved_by_manager", "=", 0],
                ["approval_manager_user", "=", frappe.session.user]
            ],
        )

        # Combine both lists
        combined_list = otpl_expense_list + expense_pull_list

        # Sort by modified date descending
        combined_list.sort(key=lambda x: x.get("modified"), reverse=True)

        # Apply pagination
        start = int(start)
        page_length = int(page_length)
        paginated_list = combined_list[start:start + page_length]

        # Clean up response - remove internal fields and add full URL for invoice_upload
        from frappe.utils import get_url
        for item in paginated_list:
            item.pop("modified", None)
            # Convert invoice_upload to full URL based on source_erp presence
            if item.get("invoice_upload"):
                if item.get("source_erp"):
                    # For Expense Pull, use source_erp as the base URL
                    source_erp = item["source_erp"]
                    if not source_erp.startswith("http"):
                        source_erp = "https://" + source_erp
                    item["invoice_upload"] = source_erp.rstrip("/") + item["invoice_upload"]
                else:
                    # For OTPL Expense, use current site's host
                    item["invoice_upload"] = get_url(item["invoice_upload"])

            # Remove source_erp from response
            item.pop("source_erp", None)

        return gen_response(
            200, "Expense approval list retrieved successfully", paginated_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read expense records")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def approve_otpl_expense():
    """
    Approve OTPL Expense or Expense Pull (auto-detects doctype)
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

        # Auto-detect if it's an Expense Pull or OTPL Expense
        is_expense_pull = frappe.db.exists("Expense Pull", expense_name)
        is_otpl_expense = frappe.db.exists("OTPL Expense", expense_name)

        if not is_expense_pull and not is_otpl_expense:
            return gen_response(500, "Expense application does not exist")

        # Handle Expense Pull records
        if is_expense_pull:
            expense_doc = frappe.get_doc("Expense Pull", expense_name)

            # Check if already approved
            if expense_doc.approved_by_manager == 1:
                return gen_response(500, "Expense is already approved")

            # Update Expense Pull
            expense_doc.amount_approved = amount_approved
            expense_doc.approved_by_manager = 1
            expense_doc.status = "Approved"
            expense_doc.save(ignore_permissions=True)

            return gen_response(200, "Expense approved successfully")

        # Handle OTPL Expense records
        else:
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
            "rejected":0,
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
                "today_work",
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
        # frappe.delete_doc("Employee Checkin", checkin_name, ignore_permissions=True)

        # Mark as a rejected
        frappe.db.set_value("Employee Checkin", checkin_name,"rejected",1)
        frappe.db.commit()

        return gen_response(200, "Check-in rejected successfully")

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to reject Employee Checkin")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_pending_approval_counts():
    """
    Get total count of pending approvals for all types
    Returns: {
        leave: count,
        expense: count,
        checkin: count,
        checkout: count,
        total: total_count
    }
    """
    try:
        current_user = frappe.session.user

        # Count OTPL Leave records
        otpl_leave_count = frappe.db.count(
            "OTPL Leave",
            filters={"status": "Pending", "approver": current_user}
        )

        # Count Leave Pull records - use get_all with count
        leave_pull_list = frappe.get_all(
            "Leave Pull",
            filters=[
                ["source_erp", "is", "set"],
                ["status", "!=", "Approved"],
                ["approver_user", "=", current_user]
            ],
            fields=["name"]
        )
        leave_pull_count = len(leave_pull_list)

        # Total leave count
        leave_count = otpl_leave_count + leave_pull_count

        # Count OTPL Expense records
        otpl_expense_count = frappe.db.count(
            "OTPL Expense",
            filters={"approved_by_manager": 0, "approval_manager": current_user}
        )

        # Count Expense Pull records - use get_all with count
        expense_pull_list = frappe.get_all(
            "Expense Pull",
            filters=[
                ["source_erp", "is", "set"],
                ["approved_by_manager", "=", 0],
                ["approval_manager_user", "=", current_user]
            ],
            fields=["name"]
        )
        expense_pull_count = len(expense_pull_list)

        # Total expense count
        expense_count = otpl_expense_count + expense_pull_count

        # Count Check-in records (log_type = IN)
        checkin_count = frappe.db.count(
            "Employee Checkin",
            filters={
                "approval_required": 1,
                "approved": 0,
                "rejected":0,
                "manager": current_user,
                "log_type": "IN"
            }
        )

        # Count Check-out records (log_type = OUT)
        checkout_count = frappe.db.count(
            "Employee Checkin",
            filters={
                "approval_required": 1,
                "approved": 0,
                "rejected":0,
                "manager": current_user,
                "log_type": "OUT"
            }
        )

        # Calculate total
        total_count = leave_count + expense_count + checkin_count + checkout_count

        # Prepare response data
        result = {
            "leave": leave_count,
            "expense": expense_count,
            "checkin": checkin_count,
            "checkout": checkout_count,
            "total": total_count
        }

        return gen_response(
            200, "Pending approval counts retrieved successfully", result
        )

    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read approval records")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_otpl_leave_approved_list(start=0, page_length=10):
    """
    Get OTPL Leave and Leave Pull Applications that have been approved
    Filters: status='Approved' and approver is the session user (for OTPL Leave)
             status='Approved' and source_erp is set (for Leave Pull)
    """
    try:
        # Get OTPL Leave approved records
        otpl_leave_list = frappe.get_all(
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
                "status",
                "approved_from_date",
                "approved_to_date",
                "modified",
            ],
            filters={"status": "Approved", "approver": frappe.session.user},
        )

        # Get Leave Pull approved records
        leave_pull_list = frappe.get_all(
            "Leave Pull",
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
                "status",
                "approved_from_date",
                "approved_to_date",
                "total_no_of_approved_days",
                "modified",
            ],
            filters=[
                ["source_erp", "is", "set"],
                ["status", "=", "Approved"],
                ["approver_user", "=", frappe.session.user]
            ],
        )

        # Combine both lists
        combined_list = otpl_leave_list + leave_pull_list

        # Sort by modified date descending
        combined_list.sort(key=lambda x: x.get("modified"), reverse=True)

        # Apply pagination
        start = int(start)
        page_length = int(page_length)
        paginated_list = combined_list[start:start + page_length]

        # Clean up response - remove internal fields
        for item in paginated_list:
            item.pop("modified", None)

        return gen_response(
            200, "Approved leave list retrieved successfully", paginated_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read leave records")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_otpl_expense_approved_list(start=0, page_length=10):
    """
    Get OTPL Expense and Expense Pull Applications that have been approved
    Filters: approved_by_manager=1 and approval_manager is the session user (for OTPL Expense)
             approved_by_manager=1 and source_erp is set (for Expense Pull)
    """
    try:
        # Get OTPL Expense approved records
        otpl_expense_list = frappe.get_all(
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
                "amount_approved",
                "modified",
            ],
            filters={"approved_by_manager": 1, "approval_manager": frappe.session.user},
        )

        # Get Expense Pull approved records
        expense_pull_list = frappe.get_all(
            "Expense Pull",
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
                "amount_approved",
                "source_erp",
                "modified",
            ],
            filters=[
                ["source_erp", "is", "set"],
                ["approved_by_manager", "=", 1],
                ["approval_manager_user", "=", frappe.session.user]
            ],
        )

        # Combine both lists
        combined_list = otpl_expense_list + expense_pull_list

        # Sort by modified date descending
        combined_list.sort(key=lambda x: x.get("modified"), reverse=True)

        # Apply pagination
        start = int(start)
        page_length = int(page_length)
        paginated_list = combined_list[start:start + page_length]

        # Clean up response - remove internal fields and add full URL for invoice_upload
        from frappe.utils import get_url
        for item in paginated_list:
            item.pop("modified", None)
            item["status"] = "Approved" if item.get("approved_by_manager") == 1 else "Pending"
            # Convert invoice_upload to full URL based on source_erp presence
            if item.get("invoice_upload"):
                if item.get("source_erp"):
                    # For Expense Pull, use source_erp as the base URL
                    source_erp = item["source_erp"]
                    if not source_erp.startswith("http"):
                        source_erp = "https://" + source_erp
                    item["invoice_upload"] = source_erp.rstrip("/") + item["invoice_upload"]
                else:
                    # For OTPL Expense, use current site's host
                    item["invoice_upload"] = get_url(item["invoice_upload"])

            # Remove source_erp from response
            item.pop("source_erp", None)

        return gen_response(
            200, "Approved expense list retrieved successfully", paginated_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read expense records")
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_employee_checkin_approved_list(start=0, page_length=10, log_type=None):
    """
    Get Employee Checkin records that have been approved
    Filters: approval_required=1, approved=1, manager is the session user
    Optional: log_type filter (IN/OUT)
    """
    try:
        filters = {
            "approval_required": 1,
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
                "today_work",
                "location",
                "approved",
                "rejected"
            ],
            start=start,
            page_length=page_length,
            order_by="modified desc",
            filters=filters,
            or_filters=[
                {"approved": 1},
                {"rejected": 1},
            ],
        )
        for item in checkin_list:
            if item.get("approved") == 1:
                item["status"] = "Approved"
            elif item.get("rejected") == 1:
                item["status"] = "Rejected"

        return gen_response(
            200, "Approved checkin list retrieved successfully", checkin_list
        )
    except frappe.PermissionError:
        return gen_response(500, "Not permitted to read Employee Checkin")
    except Exception as e:
        return exception_handler(e)
