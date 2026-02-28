import frappe
from frappe import _
from employee_self_service.mobile.v1.api_utils import (
    gen_response,
    ess_validate,
    exception_handler,
    get_employee_by_user,
    convert_timezone,
    get_system_timezone,
)
from erpnext.hr.doctype.employee.employee import (
    get_holiday_list_for_employee,
)
from frappe.utils import getdate, cint,now
from calendar import monthrange
from frappe.handler import upload_file


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_attendance_list_by_date(date=None):
    try:
        if not date:
            return gen_response(500, "date is required", [])

        emp_data = get_employee_by_user(frappe.session.user)
        employee_name = emp_data.get("name")

        # Fetch attendance records
        attendance_list = frappe.get_all(
            "Attendance",
            filters={
                "employee": employee_name,
                "attendance_date": date,
                "docstatus": 1,
            },
            fields=[
                "name",
                "DATE_FORMAT(attendance_date, '%d %W') AS attendance_date",
                "status",
                "working_hours",
                "late_entry",
            ],
        )

        # Fetch Employee Checkin records for the date using SQL
        checkins = frappe.db.sql("""
            SELECT log_type, time, location, log_location
            FROM `tabEmployee Checkin`
            WHERE employee = %s
            AND DATE(time) = %s
            ORDER BY time ASC
        """, (employee_name, date), as_dict=1)

        # Format checkin times
        employee_checkin_detail = []
        for checkin in checkins:
            checkin_time = checkin["time"].strftime("%I:%M %p") if checkin["time"] else None
            employee_checkin_detail.append({
                "log_type": checkin["log_type"],
                "time": checkin_time,
                "location": checkin.get("location"),
                "log_location": checkin.get("log_location"),
            })

        # Process attendance records
        for attendance in attendance_list:
            attendance["employee_checkin_detail"] = employee_checkin_detail
            # Remove unnecessary field
            attendance.pop("name", None)

        # If no attendance but checkins exist, create a response
        if not attendance_list and employee_checkin_detail:
            attendance_list = [{
                "attendance_date": getdate(date).strftime("%d %W"),
                "status": "No Record",
                "working_hours": None,
                "late_entry": 0,
                "employee_checkin_detail": employee_checkin_detail
            }]

        return gen_response(
            200, "Attendance data retrieved successfully", attendance_list if attendance_list else []
        )
    except Exception as e:
        return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_ess_calendar_details(year=None, month=None):
    """
    API to fetch attendance details for a given employee, year, and month.

    :param year: Year (YYYY)
    :param month: Month (MM)
    :return: JSON response with day-wise attendance status
    """
    try:
        year, month = cint(year), cint(month)
        days_in_month = monthrange(year, month)[1]
        month_start_date = f"{year}-{month:02d}-01"
        month_end_date = f"{year}-{month:02d}-{days_in_month}"

        # Get Employee Details
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name", "image", "department", "company"]
        )
        if not emp_data:
            return gen_response(404, "Employee not found")

        # Get Attendance and Holiday Data
        attendance_records = get_attendance_records(
            emp_data["name"], month_start_date, month_end_date
        )
        holidays = get_employee_holidays(
            emp_data["name"], month_start_date, month_end_date
        )

        # Prepare Response Data
        attendance_data = build_attendance_data(
            year, month, days_in_month, attendance_records, holidays
        )

        return gen_response(
            200, "ESS calendar data fetched successfully", attendance_data
        )

    except Exception as e:
        return exception_handler(e)


def get_attendance_records(employee, start_date, end_date):
    """Fetch attendance records for a given employee and date range."""
    return frappe.get_all(
        "Attendance",
        filters={
            "employee": employee,
            "attendance_date": ["between", [start_date, end_date]],
            "docstatus": 1,
        },
        fields=["attendance_date", "status"],
    )


def get_employee_holidays(employee, start_date, end_date):
    """Fetch holiday dates for a given employee and date range."""
    holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)

    if not holiday_list:
        return set()

    holiday_dates = frappe.get_all(
        "Holiday",
        filters={
            "parent": holiday_list,
            "holiday_date": ["between", [start_date, end_date]],
        },
        fields=["holiday_date"],
    )
    return {getdate(h["holiday_date"]) for h in holiday_dates}


def build_attendance_data(year, month, days_in_month, attendance_records, holidays):
    """Build the final attendance data structure."""
    attendance_data = {}

    # Populate Attendance records
    for record in attendance_records:
        date_str = getdate(record["attendance_date"]).strftime("%Y-%m-%d")
        status = record["status"]
        attendance_data[date_str] = "Absent" if status == "On Leave" else status

    # Populate Holidays and Other Days
    for day in range(1, days_in_month + 1):
        date = getdate(f"{year}-{month:02d}-{day}")
        date_str = date.strftime("%Y-%m-%d")

        if date_str not in attendance_data:
            attendance_data[date_str] = "Holiday" if date in holidays else "No Record"

    return attendance_data

# Other Employee Attendance APIs
@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_other_employee_list():
    try:
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name","is_team_leader"]
        )
        if not emp_data:
            return gen_response(404, "Employee not found")
        if emp_data.get("is_team_leader") == 1:
            other_employees = frappe.get_all("Employee",filters={"reports_to": emp_data["name"],"phone_not_working":1,"status":"Active"}, fields=["name", "employee_name"])
        else:
            other_employees = frappe.get_all("Employee",filters={"phone_not_working":1,"status":"Active"}, fields=["name", "employee_name"])

        return gen_response(
            200, "Other employee list fetched successfully", other_employees
        )
    except Exception as e:
        return exception_handler(e)

@frappe.whitelist()
@ess_validate(methods=["POST"])
def create_other_employee_attendance(
    log_type,
    employee,
    location=None,
    log_time=None,
    reason=None,
):
    try:
        if not log_time:
            log_time = now()
        emp_data = get_employee_by_user(
            frappe.session.user, fields=["name"]
        )
        if not emp_data:
            return gen_response(404, "Employee not found")

        log_doc = frappe.get_doc(
            dict(
                doctype="Other Employee Attendance",
                reporting_manager=emp_data.get("name"),
                employee = employee,
                attendance_type=log_type,
                attendance_datetime=log_time,
                location=location,
                remark=reason
            )
        ).insert(ignore_permissions=True)

        if "file" in frappe.request.files:
            file = upload_file()
            file.attached_to_doctype = "Other Employee Attendance"
            file.attached_to_name = log_doc.name
            file.attached_to_field = "attachment"
            file.save(ignore_permissions=True)
            log_doc.attachment = file.get("file_url")
            log_doc.save(ignore_permissions=True)

        return gen_response(
            200, "Other employee attendance recorded successfully", log_doc.as_dict()
        )
    except Exception as e:
        return exception_handler(e)