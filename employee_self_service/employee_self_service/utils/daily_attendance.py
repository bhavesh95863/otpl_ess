# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, add_days, get_first_day, time_diff_in_hours
from datetime import datetime, timedelta


@frappe.whitelist()
def process_daily_attendance():
	"""
	Scheduled job to process attendance for all employees.
	Runs at midnight for previous day.

	Simplified Rules:
	1. Any employee with no Employee Checkin for the day → Absent
	2. Worker + Site: checkin exists → Present (no checkout/hours needed)
	3. Worker + NOT Site: uses Allowed Overtime rules (see run_worker_attendance)
	4. Non-Worker: existing ESS Location-based rules for late/half-day
	"""
	yesterday = add_days(getdate(), -1)

	employees = frappe.get_all("Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "location", "company", "no_check_in", "staff_type","from_hours","to_hours",
			"late_arrival_threshold", "early_exit_threshold", "half_day_arrival_time", "half_day_departure_time"]
	)

	processed_count = 0
	skipped_count = 0
	error_count = 0
	absent_count = 0

	for emp in employees:
		try:
			result = process_employee_attendance(
				emp.name, emp.location, yesterday,
				emp.get("no_check_in", 0), emp.get("staff_type"), emp.get("from_hours"), emp.get("to_hours"),
				emp.get("late_arrival_threshold"), emp.get("early_exit_threshold"),
				emp.get("half_day_arrival_time"), emp.get("half_day_departure_time")
			)

			if result == "Processed":
				processed_count += 1
			elif result == "Skipped":
				skipped_count += 1
			elif result == "Absent":
				absent_count += 1
			else:
				error_count += 1

		except Exception as e:
			error_count += 1
			traceback_msg = frappe.get_traceback()
			frappe.log_error(
				title="Daily Attendance Processing Error: {0}".format(emp.name),
				message=traceback_msg
			)
			from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
			log_attendance_creation_failure(
				employee=emp.name,
				date=yesterday,
				reason="Daily attendance processing error: {0}".format(str(e)),
				error_log=traceback_msg
			)

	# Log summary
	summary = "Attendance Processing Completed for {0}\\nProcessed: {1}, Absent: {2}, Skipped: {3}, Errors: {4}, Total: {5}".format(
		yesterday, processed_count, absent_count, skipped_count, error_count, len(employees)
	)
	frappe.logger().info(summary)

	return {
		"date": str(yesterday),
		"processed": processed_count,
		"absent": absent_count,
		"skipped": skipped_count,
		"errors": error_count,
		"total": len(employees)
	}


def process_employee_attendance(employee, location, date, no_check_in=0, staff_type=None, from_hours=None, to_hours=None,
	emp_late_arrival_threshold=None, emp_early_exit_threshold=None, emp_half_day_arrival_time=None, emp_half_day_departure_time=None):
	"""
	Process attendance for a single employee.
	Returns: Processed, Skipped, Absent, or Error

	Routing:
	- Attendance or leave application already exists → Skipped
	- Worker employees → delegated to run_worker_attendance
	- no_check_in employees → auto Present
	- Non-Worker on holiday → Skipped
	- Non-Worker, non-Site → check-in + check-out required, ESS Location late/early rules, working hours
	"""
	# Check if attendance already exists (submitted) or leave application exists
	existing_attendance = frappe.db.get_value(
		"Attendance",
		{
			"employee": employee,
			"attendance_date": date,
			"docstatus": 1
		},
		"name"
	)

	if existing_attendance:
		# Attendance already created for this day, skip
		return "Skipped"

	# Self-repair, in order. Both are no-ops for an ordinary leave.
	#   1. Two approved half days on this date = a whole day away. Merge them into
	#      one full-day leave with a single full-day Leave Application, so the day
	#      below is skipped and marked "On Leave" rather than Half Day.
	#   2. Otherwise, a lone Half Day approved before the Leave Application was
	#      dropped still carries one, and it would make this day Skipped — so the
	#      half-day timing rules would never run. Retire it and process for real.
	if not repair_half_day_leave_pair(employee, date):
		remove_obsolete_half_day_leave_application(employee, date)

	# Check if leave application exists for this day
	existing_leave = frappe.db.exists(
		"Leave Application",
		{
			"employee": employee,
			"from_date": ["<=", date],
			"to_date": [">=", date],
			"docstatus": 1
		}
	)

	if existing_leave:
		# Leave application exists for this day, skip
		return "Skipped"

	# --- Worker employees: delegate to run_worker_attendance ---
	if staff_type == "Worker":
		from employee_self_service.employee_self_service.utils.worker_attendance import run_worker_attendance
		return run_worker_attendance(employee, location, date)

	# --- Driver + Noida: delegate to run_driver_attendance ---
	if staff_type == "Driver" and location == "Noida":
		from employee_self_service.employee_self_service.utils.driver_attendance import run_driver_attendance
		return run_driver_attendance(employee, date)

	# --- Field staff: checkin exists → Present, no checkin → Absent (irrespective of location) ---
	if staff_type == "Field":
		return _process_field_attendance(employee, date)

	# --- Non-Worker: no_check_in employees are auto-marked Present ---
	is_holiday_for_company_flag = is_holiday_for_company(date)
	print(f"Processing Non-Worker {employee} on {date} | no_check_in: {no_check_in} | Holiday: {is_holiday_for_company}")
	if no_check_in and not is_holiday_for_company_flag:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Auto marked present (No check-in required)",
			checkin_time=None,
			checkout_time=None
		)
		return "Processed"

	# --- Non-Worker: standard attendance processing ---
	# Get checkin records for the employee
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [date,date]]
		},
		fields=["time", "log_type", "approval_required", "approved", "rejected"],
		order_by="time asc"
	)

	# --- Non-Worker: skip if company holiday and no checkin records ---
	if is_holiday_for_company_flag and not checkins:
		return "Skipped"

	# Check if any checkin is pending approval (not yet approved or rejected)
	for checkin in checkins:
		if checkin.get("approval_required") and not checkin.get("approved") and not checkin.get("rejected"):
			return "Skipped"

	# Filter out rejected checkins — treat them as if they don't exist
	checkins = [c for c in checkins if not c.get("rejected")]

	# --- Non-Worker on holiday with checkin records ---
	if is_holiday_for_company_flag and checkins:
		has_checkin = any(c.log_type == "IN" for c in checkins)
		has_checkout = any(c.log_type == "OUT" for c in checkins)
		if has_checkin and has_checkout:
			checkin_time = None
			checkout_time = None
			for log in checkins:
				if log.log_type == "IN":
					checkin_time = log.time
					break
			for log in reversed(checkins):
				if log.log_type == "OUT":
					checkout_time = log.time
					break
			create_attendance_record(
				employee=employee,
				date=date,
				status="Present",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Present on off day (holiday with check-in and check-out)",
				checkin_time=checkin_time,
				checkout_time=checkout_time
			)
			return "Processed"
		else:
			# Checkin exists but no checkout on holiday → skip (shows as Off Day in calendar)
			return "Skipped"

	checkin_time = None
	checkout_time = None
	checkin_out_of_location = False
	checkout_out_of_location = False

	if checkins:
		# Get first IN (and whether it was an approved out-of-location punch)
		for log in checkins:
			if log.log_type == "IN":
				checkin_time = log.time
				checkin_out_of_location = bool(log.get("approval_required") and log.get("approved"))
				break

		# Get last OUT (and whether it was an approved out-of-location punch)
		for log in reversed(checkins):
			if log.log_type == "OUT":
				checkout_time = log.time
				checkout_out_of_location = bool(log.get("approval_required") and log.get("approved"))
				break

	# Rule 1: No checkin at all → Absent
	if not checkin_time and not checkout_time:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="No check-in and check-out records",
			checkin_time=None,
			checkout_time=None
		)
		return "Absent"

	# Non-Worker, non-Site: Absent if check-in or check-out is missing
	if location != "Site":
		if not checkin_time or not checkout_time:
			missing = "check-out" if checkin_time else "check-in"
			create_attendance_record(
				employee=employee,
				date=date,
				status="Absent",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Missing {0} (Non-Worker, Non-Site)".format(missing),
				checkin_time=checkin_time,
				checkout_time=checkout_time
			)
			return "Absent"

	# Get ESS Location rules (incl. employee overrides + short-leave adjustment)
	location_rules = build_location_rules(
		location, date, employee, from_hours, to_hours,
		emp_late_arrival_threshold, emp_early_exit_threshold,
		emp_half_day_arrival_time, emp_half_day_departure_time
	)

	# Non-Site out-of-location approved punches use the standard shift time so
	# the out-of-location punch is not treated as late / early.
	checkin_time, checkout_time = apply_out_of_location_shift_times(
		checkin_time, checkout_time,
		checkin_out_of_location, checkout_out_of_location,
		location_rules, date
	)

	# Determine attendance status based on rules
	status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks = determine_status(
		checkin_time, checkout_time, location_rules, employee, date
	)

	# Non-Worker employees do not need working hours
	working_hours = 0

	# Create attendance record
	create_attendance_record(
		employee=employee,
		date=date,
		status=status,
		late_entry=late_entry,
		early_exit=early_exit,
		working_hours=working_hours,
		remarks=remarks,
		checkin_time=checkin_time,
		checkout_time=checkout_time,
		extra_late_entry=extra_late_entry,
		extra_early_exit=extra_early_exit
	)

	return "Processed"


def _process_field_attendance(employee, date):
	"""
	Field staff attendance: irrespective of location.
	If any checkin exists for the day → Present (no checkout/hours needed).
	No checkin → Absent.
	"""
	has_checkin = frappe.db.sql(
		"""SELECT name FROM `tabEmployee Checkin`
		WHERE employee = %s AND time >= %s AND time < %s
		LIMIT 1""",
		(employee, date, add_days(date, 1))
	)

	if has_checkin:
		checkin_record = frappe.db.sql(
			"""SELECT time FROM `tabEmployee Checkin`
			WHERE employee = %s AND time >= %s AND time < %s
			ORDER BY time ASC LIMIT 1""",
			(employee, date, add_days(date, 1)),
			as_dict=True
		)
		checkin_time = checkin_record[0].time if checkin_record else None

		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Field Staff - Check-in recorded",
			checkin_time=checkin_time,
			checkout_time=None
		)
		return "Processed"
	else:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Field Staff - No check-in recorded",
			checkin_time=None,
			checkout_time=None
		)
		return "Absent"


def determine_status(checkin_time, checkout_time, location_rules, employee, date):
	"""
	Determine attendance status based on checkin/checkout times and ESS Location rules.
	Used for non-Worker employees only.

	Status is Present, except on an approved Half Day Leave day, where it is
	Half Day (see get_approved_half_day_leave_period).

	Late / early handling — each flag is computed INDEPENDENTLY from the ESS
	Location threshold times. Late/early on its own no longer marks a Half Day:
	- late_entry        : check-in after late_arrival_threshold.
	- early_exit        : check-out before early_exit_threshold.
	- extra_late_entry  : check-in at/after half_day_arrival_time (previously a
	  Half Day; now flagged as extra late instead).
	- extra_early_exit  : check-out at/before half_day_departure_time (previously
	  a Half Day; now flagged as extra early instead).

	The monthly count rule (late_count_for_half_day / _full_day /
	treat_late_as_half_day_after) is NOT applied here — it is handled in payroll.

	Returns: (status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks)
	"""
	status = "Present"
	late_entry = False
	early_exit = False
	extra_late_entry = False
	extra_early_exit = False
	remarks_list = []

	# An approved Half Day Leave makes the day a Half Day whatever the punches
	# say (a missing punch is caught upstream and returns Absent before we get
	# here). The 0.5-day deduction is NOT taken from this status — payroll reads
	# it off the OTPL Leave itself (approved_half_days) — so this must not be
	# double-counted. Late / early flags are still computed below, against the
	# leave-shifted thresholds from adjust_thresholds_for_half_day_leave.
	half_day_leave_period = get_approved_half_day_leave_period(employee, date)
	if half_day_leave_period:
		status = "Half Day"
		remarks_list.append("Approved {0} leave".format(half_day_leave_period))

	# If no location rules, just mark missing logs as late
	if not location_rules:
		if not checkin_time:
			late_entry = True
			remarks_list.append("Missing check-in")
		if not checkout_time:
			early_exit = True
			remarks_list.append("Missing check-out")

		remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
		return status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks

	# Get thresholds from location rules
	try:
		late_threshold = None
		early_exit_threshold = None
		half_day_arrival = None
		half_day_departure = None

		if location_rules.late_arrival_threshold:
			late_threshold = datetime.strptime(
				str(location_rules.late_arrival_threshold), "%H:%M:%S"
			).time()

		if location_rules.early_exit_threshold:
			early_exit_threshold = datetime.strptime(
				str(location_rules.early_exit_threshold), "%H:%M:%S"
			).time()

		if location_rules.half_day_arrival_time:
			half_day_arrival = datetime.strptime(
				str(location_rules.half_day_arrival_time), "%H:%M:%S"
			).time()

		if location_rules.half_day_departure_time:
			half_day_departure = datetime.strptime(
				str(location_rules.half_day_departure_time), "%H:%M:%S"
			).time()

		# Check for missing logs - always mark as late
		if not checkin_time:
			late_entry = True
			remarks_list.append("Missing check-in")

		if not checkout_time:
			early_exit = True
			remarks_list.append("Missing check-out")

		# extra_late_entry / extra_early_exit: crossing the half-day threshold.
		# Instead of marking the day Half Day, we flag it as extra late / early.
		#   check-in  at/after  half_day_arrival_time    -> extra_late_entry
		#   check-out at/before half_day_departure_time  -> extra_early_exit
		if checkin_time and half_day_arrival:
			checkin_only_time = get_datetime(checkin_time).time()
			if checkin_only_time >= half_day_arrival:
				extra_late_entry = True
				remarks_list.append(
					"Extra late entry: checked in at {0}, at/after the half-day cut-off time {1}".format(
						checkin_only_time.strftime("%H:%M"), half_day_arrival.strftime("%H:%M")
					)
				)

		if checkout_time and half_day_departure:
			checkout_only_time = get_datetime(checkout_time).time()
			if checkout_only_time <= half_day_departure:
				extra_early_exit = True
				remarks_list.append(
					"Extra early exit: checked out at {0}, at/before the half-day cut-off time {1}".format(
						checkout_only_time.strftime("%H:%M"), half_day_departure.strftime("%H:%M")
					)
				)

		# late_entry / early_exit: based on the late-arrival / early-exit threshold.
		#   check-in  after  late_arrival_threshold  -> late_entry
		#   check-out before early_exit_threshold    -> early_exit
		# If the day already qualifies as extra late / early (crossed the half-day
		# threshold), the regular flag is NOT set — extra replaces it.
		if checkin_time and late_threshold and not extra_late_entry:
			checkin_only_time = get_datetime(checkin_time).time()
			if checkin_only_time > late_threshold:
				late_entry = True
				remarks_list.append(
					"Late entry: checked in at {0}, after the late cut-off time {1}".format(
						checkin_only_time.strftime("%H:%M"), late_threshold.strftime("%H:%M")
					)
				)

		if checkout_time and early_exit_threshold and not extra_early_exit:
			checkout_only_time = get_datetime(checkout_time).time()
			if checkout_only_time < early_exit_threshold:
				early_exit = True
				remarks_list.append(
					"Early exit: checked out at {0}, before the early-exit cut-off time {1}".format(
						checkout_only_time.strftime("%H:%M"), early_exit_threshold.strftime("%H:%M")
					)
				)

	except Exception as e:
		traceback_msg = frappe.get_traceback()
		frappe.log_error(
			title="Determine Status Error: {0}".format(employee),
			message=traceback_msg
		)
		from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
		log_attendance_creation_failure(
			employee=employee,
			date=date,
			reason="Error determining attendance status: {0}".format(str(e)),
			error_log=traceback_msg
		)

	remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
	return status, late_entry, early_exit, extra_late_entry, extra_early_exit, remarks


def get_month_late_count(employee, date):
	"""Get count of late marks for employee in current month up to given date"""
	try:
		month_start = get_first_day(date)

		attendance_records = frappe.get_all(
			"Attendance",
			filters={
				"employee": employee,
				"attendance_date": ["between", [month_start, add_days(date, -1)]],
				"docstatus": 1
			},
			fields=["name"]
		)

		late_count = 0
		for att in attendance_records:
			att_doc = frappe.get_doc("Attendance", att.name)
			if (hasattr(att_doc, 'late_entry') and att_doc.late_entry) or \
			   (hasattr(att_doc, 'early_exit') and att_doc.early_exit):
				late_count += 1

		return late_count
	except:
		return 0


def create_attendance_record(employee, date, status, late_entry, early_exit, working_hours, remarks, checkin_time=None, checkout_time=None, extra_late_entry=False, extra_early_exit=False):
	"""Create and submit attendance record"""
	try:
		attendance = frappe.get_doc({
			"doctype": "Attendance",
			"employee": employee,
			"attendance_date": date,
			"status": status,
			"remarks": remarks,
			"company": frappe.db.get_value("Global Defaults", "Global Defaults", "default_company") or "",
		})

		# Set late_entry and early_exit if fields exist
		if hasattr(attendance, 'late_entry'):
			attendance.late_entry = 1 if late_entry else 0
		if hasattr(attendance, 'early_exit'):
			attendance.early_exit = 1 if early_exit else 0
		if hasattr(attendance, 'extra_late_entry'):
			attendance.extra_late_entry = 1 if extra_late_entry else 0
		if hasattr(attendance, 'extra_early_exit'):
			attendance.extra_early_exit = 1 if extra_early_exit else 0
		if hasattr(attendance, 'working_hours'):
			attendance.working_hours = working_hours

		# Set checkin_time and checkout_time if fields exist
		if hasattr(attendance, 'checkin_time') and checkin_time:
			attendance.checkin_time = checkin_time
		if hasattr(attendance, 'checkout_time') and checkout_time:
			attendance.checkout_time = checkout_time

		attendance.insert(ignore_permissions=True)
		attendance.submit()

	except Exception as e:
		traceback_msg = frappe.get_traceback()
		frappe.log_error(
			title="Create Attendance Error: {0} - {1}".format(employee, date),
			message=traceback_msg
		)
		from employee_self_service.employee_self_service.doctype.attendance_creation_failed_log.attendance_creation_failed_log import log_attendance_creation_failure
		log_attendance_creation_failure(
			employee=employee,
			date=date,
			reason="Failed to create/submit attendance record: {0}".format(str(e)),
			error_log=traceback_msg
		)
		raise


FIRST_HALF = "First Half"
SECOND_HALF = "Second Half"

# The mobile app writes half_day_period as free text and sends it ALREADY
# TRANSLATED for some locales, so the stored value is not always English
# (e.g. 'पहली छमाही'). Comparing the raw value against "First Half" silently
# misses those records — the day would get no threshold adjustment at all — so
# every read goes through normalize_half_day_period().
_HALF_DAY_PERIOD_ALIASES = {
	"first half": FIRST_HALF,
	"1st half": FIRST_HALF,
	"पहली छमाही": FIRST_HALF,
	"second half": SECOND_HALF,
	"2nd half": SECOND_HALF,
	"दूसरी छमाही": SECOND_HALF,
}


def normalize_half_day_period(period, employee=None, date=None):
	"""Map a stored half_day_period to canonical 'First Half' / 'Second Half'.

	Returns None for an empty value, and for an unrecognised one — which is
	logged, because it means a leave silently gets no threshold adjustment.
	"""
	if not period:
		return None

	normalized = _HALF_DAY_PERIOD_ALIASES.get(str(period).strip().lower())
	if not normalized:
		frappe.log_error(
			title="Unrecognised Half Day Period: {0}".format(period),
			message=(
				"OTPL Leave half_day_period {0!r} (employee {1}, date {2}) does not map to "
				"First Half / Second Half, so no attendance threshold adjustment was applied. "
				"Add it to _HALF_DAY_PERIOD_ALIASES in daily_attendance.py."
			).format(period, employee, date)
		)
	return normalized


def get_approved_short_leave_period(employee, date):
	"""Check if employee has an approved OTPL Leave with short_leave on this date.
	Returns the half_day_period ('First Half' or 'Second Half') or None."""
	result = frappe.db.get_value(
		"OTPL Leave",
		{
			"employee": employee,
			"short_leave": 1,
			"status": "Approved",
			"approved_from_date": ["<=", date],
			"approved_to_date": [">=", date]
		},
		"half_day_period"
	)
	return normalize_half_day_period(result, employee, date)


def remove_obsolete_half_day_leave_application(employee, date):
	"""Self-repair: retire the Leave Application of a Half Day OTPL Leave that no
	longer warrants one. Returns the number removed.

	A Half Day OTPL Leave used to auto-create a Leave Application. It no longer
	does — the day is now processed as real attendance (Half Day status, plus
	late / early marks against leave-shifted thresholds), exactly as Short Leave
	already worked. But leaves approved BEFORE that change still carry their
	Leave Application, and its mere existence makes attendance skip the day, so
	the half-day timing rules would never run for it.

	Rather than a one-off patch, both attendance paths call this first: the day
	is repaired the moment it is processed or re-processed.

	What counts as obsolete is decided by OTPLLeave._leave_application_range() —
	the SAME method the live approval flow uses — so repair and go-forward can
	never disagree. It keeps the Leave Application when one is still legitimately
	needed:
	  * a multi-day Half Day, whose full-leave days still need it, and
	  * Worker / Field / Noida-Driver / no-check-in employees, who are not
	    punch-based and whose attendance the Leave Application still generates.
	"""
	otpl_leave = frappe.db.get_value(
		"OTPL Leave",
		{
			"employee": employee,
			"half_day": 1,
			"status": "Approved",
			"half_day_date": date
		},
		"name"
	)
	if not otpl_leave:
		return 0

	doc = frappe.get_doc("OTPL Leave", otpl_leave)
	start = doc.approved_from_date or doc.from_date
	end = doc.approved_to_date or doc.to_date

	# A real range back means this leave still needs a Leave Application.
	la_from, la_to = doc._leave_application_range(start, end)
	if la_from and la_to:
		return 0

	removed = _detach_leave_applications(doc)
	return removed


def _detach_leave_applications(doc):
	"""Cancel + delete every Leave Application of an OTPL Leave, and clear its
	reference field. Returns how many were removed.

	The in-memory doc is cleared too, not just the row: a later
	_create_regular_leave_applications() on the same object would otherwise append
	to a stale reference list and resurrect the name of a just-deleted application.
	"""
	removed = 0
	for leave_app_name in _linked_leave_applications(doc):
		try:
			leave_app = frappe.get_doc("Leave Application", leave_app_name)
			leave_app.flags.ignore_permissions = True
			# Sanctioned detach: validate_leave_application_cancel() otherwise
			# refuses to cancel an auto-created application whose OTPL Leave is
			# still Approved.
			leave_app.flags.ignore_otpl_leave_link = True

			if leave_app.docstatus == 1:
				leave_app.cancel()

			frappe.delete_doc(
				"Leave Application", leave_app_name,
				force=True, ignore_permissions=True
			)
			removed += 1
		except Exception:
			frappe.log_error(
				title="Could not remove Leave Application: {0}".format(leave_app_name),
				message=frappe.get_traceback()
			)

	if removed:
		doc.leave_applications = ""
		doc.db_set("leave_applications", "", update_modified=False)

	return removed


def half_day_merge_supported():
	"""True when the half-day pair merge is safe to attempt.

	The merge writes `merged_into` on the half day it retires. That field arrives
	with a doctype change, so on a site where `bench migrate` has not run the
	column does not exist and the write fails — AFTER the old Leave Applications
	have been deleted. Every caller checks this BEFORE taking any destructive
	action, so a un-migrated site simply leaves the pair alone rather than
	half-merging it.
	"""
	return frappe.db.has_column("OTPL Leave", "merged_into")


def repair_half_day_leave_pair(employee, date):
	"""Self-repair: collapse two approved half days on the SAME date into one
	full-day leave. Returns 1 if a pair was merged, else 0.

	Two approved half days on one date mean the employee was away the whole day.
	Approving the second half now merges the pair on the spot (merge_half_day_pair).
	Pairs approved BEFORE that change are still two separate half days, each
	carrying its own half-day Leave Application, so the day reads as a "Half Day"
	the employee half-worked when in fact they were away all day.

	Attendance heals it the moment the day is processed or re-processed: both
	half-day Leave Applications are retired, a NEW full-day OTPL Leave is created
	from the pair (both halves are then Cancelled with `merged_into` pointing at
	it), and that leave gets a real approved Leave Application — leaving an
	ordinary full-day leave ("On Leave" attendance, one day of CL). No patch, no
	migration.

	Whether a pair may be merged is decided by OTPLLeave._find_opposite_half_day()
	— the same method the approval flow uses — so repair and go-forward can never
	disagree. In particular a half day that is one end of a LONGER leave is never
	merged, because collapsing it would destroy that leave's full-leave days.
	"""
	# HARD GATE. Nothing below this line may run on a site where the merge cannot
	# complete: the destructive step (deleting the two half-day Leave Applications)
	# happens before the full-day one is created, so a failure part-way would leave
	# the day with no Leave Application at all.
	if not half_day_merge_supported():
		frappe.log_error(
			title="Half day pair merge skipped: schema not migrated",
			message=(
				"OTPL Leave has no `merged_into` column, so two half days on {0} for "
				"{1} were left as-is rather than risk a half-finished merge. "
				"Run `bench migrate` to enable it."
			).format(date, employee)
		)
		return 0

	rows = frappe.get_all(
		"OTPL Leave",
		filters={
			"employee": employee,
			"half_day": 1,
			"status": "Approved",
			"half_day_date": date
		},
		fields=["name"],
		order_by="creation asc"
	)
	if len(rows) < 2:
		return 0

	first = frappe.get_doc("OTPL Leave", rows[0].name)
	other = first._find_opposite_half_day()
	if not other:
		return 0
	second = frappe.get_doc("OTPL Leave", other)

	# The merge deletes the two half-day Leave Applications and creates one full-day
	# one. Those steps cannot be reordered (the old ones must go before the new one,
	# or ERPNext rejects it as overlapping leave), so they are made ATOMIC instead:
	# any failure rewinds to the savepoint and the day is left exactly as found.
	from employee_self_service.employee_self_service.doctype.otpl_leave.otpl_leave import (
		merge_half_day_pair,
	)

	frappe.db.sql("SAVEPOINT half_day_pair_merge")
	try:
		# Legacy half days may still carry a Leave Application of their own; they
		# must go before the full-day one is created.
		_detach_leave_applications(first)
		_detach_leave_applications(second)

		merged = merge_half_day_pair(first, second)
		if not merged:
			raise RuntimeError("merge did not produce a full-day leave")

		# The whole point of the merge is the full-day Leave Application. If it was
		# not created, the day would silently end up with NO leave at all (and be
		# marked Absent — which is exactly how this went wrong before). Treat that
		# as a failure and rewind rather than persist it.
		if not frappe.db.get_value("OTPL Leave", merged, "leave_applications"):
			raise RuntimeError(
				"merged leave {0} has no Leave Application".format(merged)
			)
	except Exception:
		frappe.db.sql("ROLLBACK TO SAVEPOINT half_day_pair_merge")
		frappe.log_error(
			title="Could not merge half day pair: {0} on {1} (rolled back)".format(
				employee, date
			),
			message=frappe.get_traceback()
		)
		return 0

	frappe.db.sql("RELEASE SAVEPOINT half_day_pair_merge")
	return 1


def _linked_leave_applications(doc):
	"""Every Leave Application belonging to an OTPL Leave.

	Reads the `leave_applications` reference field AND searches by the
	description stamp make_leave_application() writes, so an application whose
	reference was never recorded back is still found.
	"""
	names = [n.strip() for n in (doc.leave_applications or "").split(",") if n.strip()]

	for row in frappe.get_all(
		"Leave Application",
		filters={
			"description": "Auto-created from OTPL Leave: {0}".format(doc.name),
			"docstatus": ["<", 2]
		},
		fields=["name"]
	):
		if row.name not in names:
			names.append(row.name)

	return [n for n in names if frappe.db.exists("Leave Application", n)]


def get_approved_half_day_leave_period(employee, date):
	"""Check if employee has an approved Half Day OTPL Leave on this date.
	Returns the half_day_period ('First Half' or 'Second Half') or None.

	Keyed on half_day_date, which is the ONE day of the leave that is a half
	day — a Half Day may also be the first/last day of a longer leave, whose
	other days are ordinary full-day Leave Applications and get skipped upstream
	as normal. Keying on half_day_date (rather than the approved range) is also
	what OTPL Payroll does for approved_half_days, so the two agree by
	construction.

	The half day itself creates no Leave Application, so the day reaches normal
	attendance processing: shifted thresholds + a Half Day status.
	"""
	result = frappe.db.get_value(
		"OTPL Leave",
		{
			"employee": employee,
			"half_day": 1,
			"status": "Approved",
			"half_day_date": date
		},
		"half_day_period"
	)
	return normalize_half_day_period(result, employee, date)


def _shift_time(time_val, offset):
	"""Add/subtract offset from a timedelta or 'HH:MM:SS' string, returned as a timedelta."""
	if not time_val:
		return time_val
	# Convert to timedelta seconds for arithmetic
	if isinstance(time_val, timedelta):
		total = time_val
	else:
		parts = str(time_val).split(":")
		total = timedelta(hours=int(parts[0]), minutes=int(parts[1]),
						  seconds=int(float(parts[2])) if len(parts) > 2 else 0)
	new_total = total + offset
	# Clamp to 0–24h range
	if new_total.total_seconds() < 0:
		new_total = timedelta(0)
	if new_total.total_seconds() > 86400:
		new_total = timedelta(hours=24)
	return new_total


def adjust_thresholds_for_half_day_leave(location_rules, half_day_leave_period):
	"""Adjust location_rules timing thresholds for an approved Half Day Leave.

	The half the employee is present for is half the shift, i.e. the leave
	boundary sits 4h30m from the shift edge. A 1-hour window past that boundary
	is a normal late entry / early exit; beyond that window it is a double
	(extra) late entry / early exit. Example, shift 09:30–18:00:

	First Half leave (off 09:30–14:00, must check in by 14:00):
	  boundary = shift_start + 4h30m        = 14:00
	  check-in <= 14:00                     -> on time
	  14:00 < check-in < 15:00              -> late_entry
	  check-in >= 15:00                     -> extra_late_entry (double)
	  no check-in at all                    -> Absent (handled upstream)

	Second Half leave (off 13:30–18:00, must check out after 13:30):
	  boundary = shift_end - 4h30m          = 13:30
	  check-out >= 13:30                    -> on time
	  12:30 < check-out < 13:30             -> early_exit
	  check-out <= 12:30                    -> extra_early_exit (double)
	  no check-out at all                   -> Absent (handled upstream)

	These map straight onto the existing late_arrival_threshold /
	half_day_arrival_time (and early_exit_threshold / half_day_departure_time)
	pair, so determine_status needs no special-casing. The opposite side of the
	shift keeps the location's normal thresholds — on a First Half leave the
	employee still works through to the usual shift end, and vice versa.
	"""
	HALF_DAY_LEAVE_OFFSET = timedelta(hours=4, minutes=30)
	DOUBLE_MARK_WINDOW = timedelta(hours=1)

	if half_day_leave_period == "First Half":
		# Boundary (shift start + 4h30m) is the late cut-off; one hour later the
		# late entry becomes a double. Computed before shift_start_time is moved.
		boundary = _shift_time(location_rules.shift_start_time, HALF_DAY_LEAVE_OFFSET)
		location_rules.late_arrival_threshold = boundary
		location_rules.half_day_arrival_time = _shift_time(boundary, DOUBLE_MARK_WINDOW)
		location_rules.shift_start_time = boundary

	elif half_day_leave_period == "Second Half":
		# Boundary (shift end - 4h30m) is the early-exit cut-off; one hour before
		# it the early exit becomes a double. Computed before shift_end_time moves.
		boundary = _shift_time(location_rules.shift_end_time, -HALF_DAY_LEAVE_OFFSET)
		location_rules.early_exit_threshold = boundary
		location_rules.half_day_departure_time = _shift_time(boundary, -DOUBLE_MARK_WINDOW)
		location_rules.shift_end_time = boundary

	return location_rules


def adjust_thresholds_for_short_leave(location_rules, short_leave_period):
	"""Adjust location_rules timing thresholds based on short leave period.

	On a short-leave day the regular late-arrival / early-exit thresholds are
	NOT considered at all. Only the half-day cut-off is checked, and it is
	derived directly from the shift timing (not from the existing threshold
	fields):

	First Half short leave: employee is off in the morning and is expected in
	~2 hours after shift start. The half-day arrival cut-off becomes
	shift_start_time + 2h (e.g. 9:30 → 11:30); only an arrival at/after that
	is flagged (extra_late_entry). Regular late-arrival is disabled.

	Second Half short leave: employee leaves ~2 hours before shift end. The
	half-day departure cut-off becomes shift_end_time - 2h (e.g. 18:00 →
	16:00); only a departure at/before that is flagged (extra_early_exit).
	Regular early-exit is disabled.
	"""
	SHORT_LEAVE_OFFSET = timedelta(hours=2)

	if short_leave_period == "First Half":
		# Half-day arrival cut-off is derived from shift start (+2h), computed
		# before shift_start_time itself is shifted.
		location_rules.half_day_arrival_time = _shift_time(location_rules.shift_start_time, SHORT_LEAVE_OFFSET)
		# Regular late-arrival is not considered on a short-leave day.
		location_rules.late_arrival_threshold = None
		location_rules.shift_start_time = _shift_time(location_rules.shift_start_time, SHORT_LEAVE_OFFSET)

	elif short_leave_period == "Second Half":
		# Half-day departure cut-off is derived from shift end (-2h), computed
		# before shift_end_time itself is shifted.
		location_rules.half_day_departure_time = _shift_time(location_rules.shift_end_time, -SHORT_LEAVE_OFFSET)
		# Regular early-exit is not considered on a short-leave day.
		location_rules.early_exit_threshold = None
		location_rules.shift_end_time = _shift_time(location_rules.shift_end_time, -SHORT_LEAVE_OFFSET)

	return location_rules


def build_location_rules(location, date, employee, from_hours=None, to_hours=None,
	emp_late_arrival_threshold=None, emp_early_exit_threshold=None,
	emp_half_day_arrival_time=None, emp_half_day_departure_time=None):
	"""Load the ESS Location rules for an employee/date and return a ready-to-use
	rules doc.

	This is the single source of truth for how attendance thresholds are resolved.
	It applies, in order:
	  1. Employee working-hours override (from_hours / to_hours)
	  2. Employee-level threshold overrides
	  3. Any approved short-leave threshold shift for the date
	  4. Any approved half-day-leave threshold shift for the date

	The scheduled daily job AND the reprocess/backfill paths all call this so
	short-leave / half-day-leave handling can never drift between them.

	Returns the (possibly mutated) ESS Location doc, or None when the location
	has no ESS Location rules.
	"""
	if not location or not frappe.db.exists("ESS Location", location):
		return None

	location_rules = frappe.get_doc("ESS Location", location)
	if from_hours and to_hours:
		location_rules.shift_start_time = from_hours
		location_rules.shift_end_time = to_hours
		location_rules.late_arrival_threshold = from_hours
		location_rules.early_exit_threshold = to_hours
	# Override with employee-level thresholds if defined
	if emp_late_arrival_threshold:
		location_rules.late_arrival_threshold = emp_late_arrival_threshold
	if emp_early_exit_threshold:
		location_rules.early_exit_threshold = emp_early_exit_threshold
	if emp_half_day_arrival_time:
		location_rules.half_day_arrival_time = emp_half_day_arrival_time
	if emp_half_day_departure_time:
		location_rules.half_day_departure_time = emp_half_day_departure_time

	# Adjust thresholds if employee has an approved short leave for this date
	short_leave_period = get_approved_short_leave_period(employee, date)
	if short_leave_period:
		location_rules = adjust_thresholds_for_short_leave(location_rules, short_leave_period)

	# Adjust thresholds if employee has an approved half day leave for this date
	half_day_leave_period = get_approved_half_day_leave_period(employee, date)
	if half_day_leave_period:
		location_rules = adjust_thresholds_for_half_day_leave(location_rules, half_day_leave_period)

	return location_rules


def _datetime_at(date, time_val):
	"""Build a datetime for `date` at the given time-of-day.

	`time_val` may be a timedelta (how ESS Location Time fields load) or a
	'HH:MM:SS' / 'HH:MM:SS.ffffff' string.
	"""
	d = getdate(date)
	if isinstance(time_val, timedelta):
		total_seconds = int(time_val.total_seconds())
	else:
		parts = str(time_val).split(":")
		hours = int(parts[0])
		minutes = int(parts[1]) if len(parts) > 1 else 0
		seconds = int(float(parts[2])) if len(parts) > 2 else 0
		total_seconds = hours * 3600 + minutes * 60 + seconds
	return datetime(d.year, d.month, d.day) + timedelta(seconds=total_seconds)


def apply_out_of_location_shift_times(checkin_time, checkout_time,
	checkin_out_of_location, checkout_out_of_location, location_rules, date):
	"""Non-Site out-of-location handling.

	When a Non-Site employee's check-in (or check-out) punch was made out of
	location and approved, that punch's time is replaced by the standard shift
	start (or end) time from the ESS Location, so the out-of-location punch is
	not treated as late arrival / early exit. The other punch is evaluated
	normally.
	"""
	if not location_rules:
		return checkin_time, checkout_time

	if checkin_out_of_location and location_rules.get("shift_start_time"):
		checkin_time = _datetime_at(date, location_rules.shift_start_time)

	if checkout_out_of_location and location_rules.get("shift_end_time"):
		checkout_time = _datetime_at(date, location_rules.shift_end_time)

	return checkin_time, checkout_time


def is_holiday_for_company(date):
	"""Check if date is a holiday based on Global Defaults company holiday list"""
	try:
		from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday as check_holiday

		# Get default company from Global Defaults
		default_company = frappe.db.get_single_value("Global Defaults", "default_company")
		if not default_company:
			return False

		# Get company's default holiday list
		holiday_list = frappe.get_cached_value("Company", default_company, "default_holiday_list")
		if not holiday_list:
			return False

		return check_holiday(holiday_list, date)
	except:
		pass

	return False

@frappe.whitelist()
def is_holiday_check_api(date):
	return is_holiday_for_company(date)