import frappe
import json


def after_employee_checkin_insert(doc, method):
    # Validate Worker check-in/check-out with time adjustments
    from employee_self_service.employee_self_service.utils.worker_attendance import validate_worker_checkin
    
    is_valid, message, adjusted_time = validate_worker_checkin(doc.employee, doc.log_type, doc.time)
    
    if not is_valid:
        # Delete the checkin if not valid
        frappe.db.delete("Employee Checkin", {"name": doc.name})
        frappe.db.commit()
        frappe.throw(message)
    
    # Adjust time if needed
    if adjusted_time and adjusted_time != doc.time:
        doc.time = adjusted_time
        doc.save(ignore_permissions=True)
        frappe.msgprint(message, alert=True, indicator="orange")
    
    if doc.reason or doc.today_work:
        doc.approval_required = 1
        doc.manager = frappe.db.get_value("Employee", doc.requested_from, "user_id")
        doc.save(ignore_permissions=True)

    # Sync to remote ERPs as Leader Location if employee is team leader
    sync_leader_location_to_remote(doc)


def sync_leader_location_to_remote(checkin_doc):
    """
    Sync Employee Checkin to remote ERPs as Leader Location if employee is team leader
    """
    try:
        # Check if employee is team leader
        employee = checkin_doc.employee
        if not employee:
            return

        is_team_leader = frappe.get_cached_value("Employee", employee, "is_team_leader")
        if not is_team_leader:
            return

        # Get employee company
        company = frappe.get_cached_value("Employee", employee, "company")

        # Prepare leader location data - send employee ID, not Employee Pull
        leader_location_data = {
            "employee": employee,  # Send actual Employee ID
            "company": company,
            "datetime": checkin_doc.time,
            "location": checkin_doc.get("location") or "Unknown",
        }

        # Get all enabled ERP Sync Settings
        sync_settings = frappe.get_all(
            "ERP Sync Settings",
            filters={"enabled": 1, "sync_leader_location": 1},
            fields=["name"],
        )

        if not sync_settings:
            return

        # Queue sync for each remote ERP
        for settings in sync_settings:
            # Create queue entry
            queue_doc = frappe.get_doc(
                {
                    "doctype": "ERP Sync Queue",
                    "erp_sync_settings": settings.name,
                    "doctype_name": "Leader Location",
                    "document_name": checkin_doc.name,
                    "sync_action": "Create/Update",
                    "status": "Pending",
                    "retry_count": 0,
                    "sync_data": json.dumps(leader_location_data, default=str),
                }
            )
            queue_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            # Enqueue the sync job - immediate execution
            frappe.enqueue(
                "employee_self_service.employee_self_service.utils.erp_sync.process_sync_queue_item",
                queue="default",
                timeout=300,
                queue_name=queue_doc.name,
                is_async=True,
                now=True,  # Execute immediately
            )

    except Exception as e:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="Error syncing Leader Location for checkin {0}".format(
                checkin_doc.name
            ),
        )


@frappe.whitelist()
def approve_checkin(checkin_name, log_time=None):
    """Approve an employee checkin"""
    doc = frappe.get_doc("Employee Checkin", checkin_name)

    # Check if user has permission to approve
    if not (
        frappe.session.user == "Administrator" or frappe.session.user == doc.manager
    ):
        frappe.throw("You don't have permission to approve this check-in")

    # Update log time if provided
    if log_time:
        doc.time = log_time

    # Set approved field
    doc.approved = 1
    doc.save(ignore_permissions=True)

    # Process attendance for the employee on that day
    from employee_self_service.employee_self_service.utils.daily_attendance import (
        process_employee_attendance,
    )
    from frappe.utils import getdate

    attendance_date = getdate(doc.time)
    employee = doc.employee
    employee_location = frappe.db.get_value("Employee", employee, "location")

    try:
        # Check if attendance already exists
        existing_attendance = frappe.db.get_value(
            "Attendance",
            {"employee": employee, "attendance_date": attendance_date, "docstatus": 1},
            ["name", "leave_application"],
            as_dict=True,
        )

        if existing_attendance:
            if existing_attendance.leave_application:
                # Leave-based attendance exists, don't process
                return {
                    "status": "success",
                    "message": "Check-in approved successfully. Attendance not processed due to existing leave application.",
                }
            else:
                # Cancel existing attendance to reprocess
                att_doc = frappe.get_doc("Attendance", existing_attendance.name)
                att_doc.cancel()
                frappe.db.commit()

        # Process attendance for the day
        result = process_employee_attendance(
            employee, employee_location, attendance_date
        )

        return {
            "status": "success",
            "message": "Check-in approved and attendance processed successfully",
            "attendance_result": result,
        }
    except Exception as e:
        frappe.log_error(
            title="Approve Checkin - Attendance Processing Error: {0}".format(employee),
            message=frappe.get_traceback(),
        )
        return {
            "status": "success",
            "message": "Check-in approved successfully but attendance processing failed. Please check error logs.",
        }

    return {"status": "success", "message": "Check-in approved successfully"}
