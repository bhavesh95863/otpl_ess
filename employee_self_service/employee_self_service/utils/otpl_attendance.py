import frappe

def after_employee_checkin_insert(doc, method):
    if doc.requested_from and doc.reason:
        doc.approval_required = 1
        doc.manager = frappe.db.get_value("Employee",doc.requested_from, "user_id")
        doc.save(ignore_permissions=True)

@frappe.whitelist()
def approve_checkin(checkin_name, log_time=None):
    """Approve an employee checkin"""
    doc = frappe.get_doc("Employee Checkin", checkin_name)
    
    # Check if user has permission to approve
    if not (frappe.session.user == 'Administrator' or frappe.session.user == doc.manager):
        frappe.throw("You don't have permission to approve this check-in")
    
    # Update log time if provided
    if log_time:
        doc.time = log_time
    
    # Set approved field
    doc.approved = 1
    doc.save(ignore_permissions=True)
    
    # Process attendance for the employee on that day
    from employee_self_service.employee_self_service.utils.daily_attendance import process_employee_attendance
    from frappe.utils import getdate
    
    attendance_date = getdate(doc.time)
    employee = doc.employee
    employee_location = frappe.db.get_value("Employee", employee, "location")
    
    try:
        # Check if attendance already exists
        existing_attendance = frappe.db.get_value(
            "Attendance",
            {
                "employee": employee,
                "attendance_date": attendance_date,
                "docstatus": 1
            },
            ["name", "leave_application"],
            as_dict=True
        )
        
        if existing_attendance:
            if existing_attendance.leave_application:
                # Leave-based attendance exists, don't process
                return {"status": "success", "message": "Check-in approved successfully. Attendance not processed due to existing leave application."}
            else:
                # Cancel existing attendance to reprocess
                att_doc = frappe.get_doc("Attendance", existing_attendance.name)
                att_doc.cancel()
                frappe.db.commit()
        
        # Process attendance for the day
        result = process_employee_attendance(employee, employee_location, attendance_date)
        
        return {
            "status": "success", 
            "message": "Check-in approved and attendance processed successfully",
            "attendance_result": result
        }
    except Exception as e:
        frappe.log_error(
            title="Approve Checkin - Attendance Processing Error: {0}".format(employee),
            message=frappe.get_traceback()
        )
        return {
            "status": "success", 
            "message": "Check-in approved successfully but attendance processing failed. Please check error logs."
        }
    
    return {"status": "success", "message": "Check-in approved successfully"}