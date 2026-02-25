import frappe
import json
import math
import requests
from frappe import _


def after_employee_checkin_insert(doc, method):
    # Validate Worker check-in/check-out with time adjustments
    from employee_self_service.employee_self_service.utils.worker_attendance import validate_worker_checkin
    if doc.auto_created_entry == 1:
        fetch_employee_details(doc)
        doc.save(ignore_permissions=True)
        return
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
    distance_validation(doc)
    fetch_employee_details(doc)
    doc.save(ignore_permissions=True)


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

def distance_validation(doc):
    if not doc.employee or not doc.location or doc.auto_created_entry == 1:
        return

    last_checkin = frappe.db.get_all(
        "Employee Checkin",
        filters={
            "employee": doc.employee,
            "log_type":"IN",
            "name": ["!=", doc.name],
            "location": ["!=", ""]
        },
        fields=["location"],
        order_by="creation desc",
        limit=1
    )

    if not last_checkin:
        return

    try:
        current_lat, current_lon = map(float, doc.location.split(","))
        last_lat, last_lon = map(float, last_checkin[0].location.split(","))
    except Exception:
        return

    address = get_address_from_lat_long(current_lat, current_lon)
    if address:
        doc.address = address

    is_team_leader = frappe.db.get_value(
        "Employee",
        doc.employee,
        "is_team_leader"
    )

    if not is_team_leader:
        return

    distance = calculate_distance_km(
        current_lat, current_lon,
        last_lat, last_lon
    )
    allow_distance = frappe.db.get_single_value("Employee Self Service Settings","distance")
    if distance > allow_distance:
        doc.team_leader_location_changed = 1
        doc.distance_different = distance

def calculate_distance_km(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in KM

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))


    return R * c


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

def fetch_employee_details(doc):
    employee_details = frappe.db.get_value(
        "Employee",
        doc.employee,
        ["name","business_vertical","sales_order","external_sales_order",
        "external_order","external_so","external_business_vertical","staff_type","location","external_reporting_manager","external_report_to","reports_to"],as_dict=True)


    # field mapping
    if employee_details.external_sales_order:
        doc.sales_order = employee_details.external_order
    else:
        doc.sales_order = employee_details.sales_order

    if employee_details.external_business_vertical:
        doc.business_vertical = employee_details.external_business_vertical
    else:
        doc.business_vertical = employee_details.business_vertical

    if employee_details.external_reporting_manager:
        doc.reports_to = employee_details.external_report_to
    else:
        doc.reports_to = employee_details.reports_to

    doc.employee_location = employee_details.location
    doc.staff_type = employee_details.staff_type

def get_address_from_lat_long(lat, lon):
    try:
        url = "https://nominatim.openstreetmap.org/reverse"

        params = {
            "format": "json",
            "lat": lat,
            "lon": lon,
            "zoom": 18,
            "addressdetails": 1
        }

        headers = {
            "User-Agent": "Frappe-HRMS-Checkin/1.0"
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if data.get("display_name"):
            return data["display_name"]

        address = data.get("address", {})
        parts = [
            address.get("road"),
            address.get("city"),
            address.get("state"),
            address.get("postcode"),
            address.get("country"),
        ]

        return ", ".join([p for p in parts if p])

    except Exception:
        frappe.log_error(
            title="Reverse Geocoding Failed",
            message=frappe.get_traceback()
        )

    return None

def validate(doc,method):
    emp = frappe.db.get_value(
        "Employee",
        doc.employee,
        ["location","staff_type","is_team_leader","employee_availability"],as_dict=True
    )
    if (
        emp.location == "Site" and emp.staff_type == "Worker" and emp.is_team_leader != 1 and
        emp.employee_availability == "On Leave"
    ):
        frappe.throw(_("You are on leave today"))

    # Validate: each employee cannot have more than one IN or OUT log on the same date
    if doc.time and doc.log_type in ("IN", "OUT"):
        from frappe.utils import getdate, add_days, get_datetime_str
        checkin_date = getdate(doc.time)
        day_start = get_datetime_str(checkin_date)
        day_end = get_datetime_str(add_days(checkin_date, 1))

        conditions = """employee = %(employee)s AND log_type = %(log_type)s
            AND time >= %(day_start)s AND time < %(day_end)s"""
        query_params = {
            "employee": doc.employee,
            "log_type": doc.log_type,
            "day_start": day_start,
            "day_end": day_end,
        }
        if not doc.is_new():
            conditions += " AND name != %(name)s"
            query_params["name"] = doc.name

        existing = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabEmployee Checkin` WHERE " + conditions,
            query_params
        )[0][0]

        if existing:
            frappe.throw(
                _("Employee {0} already has a {1} record for {2}. Only one {1} entry is allowed per day.").format(
                    doc.employee, doc.log_type, checkin_date
                )
            )