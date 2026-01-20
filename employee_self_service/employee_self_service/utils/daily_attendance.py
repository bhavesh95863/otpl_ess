# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, add_days, get_first_day, time_diff_in_hours
from datetime import datetime

@frappe.whitelist()
def process_daily_attendance():
	"""
	Scheduled job to process attendance for all employees
	Runs at midnight for previous day
	"""
	yesterday = add_days(getdate(), -1)
	
	# Check if yesterday is a holiday based on Global Defaults company
	if is_holiday_for_company(yesterday):
		return {
			"date": str(yesterday),
			"message": "Holiday - Attendance processing skipped",
			"processed": 0,
			"absent": 0,
			"skipped": 0,
			"errors": 0,
			"total": 0
		}
	
	employees = frappe.get_all("Employee", 
		filters={"status": "Active"}, 
		fields=["name", "employee_name", "location", "company", "no_check_in", "staff_type"]
	)
	
	processed_count = 0
	skipped_count = 0
	error_count = 0
	absent_count = 0
	
	for emp in employees:
		try:
			result = process_employee_attendance(emp.name, emp.location, yesterday, emp.get("no_check_in", 0), emp.get("staff_type"))
			
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
			frappe.log_error(
				title="Daily Attendance Processing Error: {0}".format(emp.name),
				message=frappe.get_traceback()
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


def process_employee_attendance(employee, location, date, no_check_in=0, staff_type=None):
	"""
	Process attendance for a single employee
	Returns: Processed, Skipped, Absent, or Error
	"""
	# Check if attendance already exists from leave application
	existing_attendance = frappe.db.get_value(
		"Attendance",
		{
			"employee": employee,
			"attendance_date": date,
			"docstatus": 1
		},
		["name", "leave_application"],
		as_dict=True
	)
	
	if existing_attendance:
		if existing_attendance.leave_application:
			# Leave-based attendance exists, skip
			return "Skipped"
		else:
			# Cancel non-leave attendance to reprocess
			try:
				att_doc = frappe.get_doc("Attendance", existing_attendance.name)
				att_doc.cancel()
				frappe.db.commit()
			except Exception as e:
				frappe.log_error(
					title="Cancel Existing Attendance: {0}".format(employee),
					message=frappe.get_traceback()
				)
	
	# Check for Worker-specific attendance processing
	from employee_self_service.employee_self_service.utils.worker_attendance import process_worker_attendance_with_hours
	
	worker_result = process_worker_attendance_with_hours(employee, location, date)
	if worker_result:
		return worker_result
	
	# If employee has no_check_in enabled, directly mark as present
	if no_check_in:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Auto marked present (No check-in required)"
		)
		return "Processed"
	
	# Get checkin records for the employee
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [date, add_days(date, 1)]]
		},
		fields=["time", "log_type", "approval_required", "approved"],
		order_by="time asc"
	)
	
	# Check if any checkin requires approval and is not approved yet
	for checkin in checkins:
		if checkin.get("approval_required") and not checkin.get("approved"):
			# Skip attendance processing for this employee
			return "Skipped"
	
	checkin_time = None
	checkout_time = None
	
	if checkins:
		# Get first IN
		for log in checkins:
			if log.log_type == "IN":
				checkin_time = log.time
				break
		
		# Get last OUT
		for log in reversed(checkins):
			if log.log_type == "OUT":
				checkout_time = log.time
				break
	
	# If no checkin and no checkout, mark as absent
	if not checkin_time and not checkout_time:
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="No check-in and check-out records"
		)
		return "Absent"
	
	# For non-Worker employees with location != "Site": Mark as Absent if check-in or check-out is missing
	if staff_type != "Worker" and location and location != "Site":
		if not checkin_time or not checkout_time:
			create_attendance_record(
				employee=employee,
				date=date,
				status="Absent",
				late_entry=False,
				early_exit=False,
				working_hours=0,
				remarks="Missing check-in or check-out (Non-Worker)"
			)
			return "Absent"
	
	# Get ESS Location rules if location is set
	location_rules = None
	if location:
		if frappe.db.exists("ESS Location", location):
			location_rules = frappe.get_doc("ESS Location", location)
	
	# Determine attendance status based on rules
	status, late_entry, early_exit, remarks = determine_status(
		checkin_time, checkout_time, location_rules, employee, date
	)
	
	# Calculate working hours
	working_hours = 0
	if checkin_time and checkout_time:
		try:
			working_hours = time_diff_in_hours(
				get_datetime(checkout_time),
				get_datetime(checkin_time)
			)
			if working_hours < 0:
				working_hours = 0
		except:
			working_hours = 0
	
	# Create attendance record
	create_attendance_record(
		employee=employee,
		date=date,
		status=status,
		late_entry=late_entry,
		early_exit=early_exit,
		working_hours=working_hours,
		remarks=remarks
	)
	
	return "Processed"


def determine_status(checkin_time, checkout_time, location_rules, employee, date):
	"""
	Determine attendance status based on checkin/checkout times and location rules
	Returns: (status, late_entry, early_exit, remarks)
	"""
	status = "Present"
	late_entry = False
	early_exit = False
	remarks_list = []
	
	# If no location rules, just mark missing logs as late
	if not location_rules:
		if not checkin_time:
			late_entry = True
			remarks_list.append("Missing check-in")
		if not checkout_time:
			early_exit = True
			remarks_list.append("Missing check-out")
		
		remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
		return status, late_entry, early_exit, remarks
	
	# Get thresholds from location rules
	try:
		late_threshold = None
		early_exit_threshold = None
		half_day_arrival = None
		half_day_departure = None
		treat_late_as_half_day_after = location_rules.get('treat_late_as_half_day_after') or 5
		
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
		
		# Check for half day conditions
		if checkin_time and half_day_arrival:
			checkin_only_time = get_datetime(checkin_time).time()
			if checkin_only_time >= half_day_arrival:
				status = "Half Day"
				remarks_list.append("Arrived at/after {0}".format(half_day_arrival))
		
		if checkout_time and half_day_departure:
			checkout_only_time = get_datetime(checkout_time).time()
			if checkout_only_time <= half_day_departure:
				status = "Half Day"
				remarks_list.append("Left at/before {0}".format(half_day_departure))
		
		# Check for late arrival (if not already half day)
		if checkin_time and late_threshold and status != "Half Day":
			checkin_only_time = get_datetime(checkin_time).time()
			if checkin_only_time > late_threshold:
				# Check if should treat as half day
				current_month_late_count = get_month_late_count(employee, date)
				if current_month_late_count >= treat_late_as_half_day_after:
					status = "Half Day"
					remarks_list.append(
						"Late arrival treated as Half Day (exceeded {0} late marks)".format(
							treat_late_as_half_day_after
						)
					)
				else:
					late_entry = True
					remarks_list.append("Late arrival after {0}".format(late_threshold))
		
		# Check for early exit (if not already half day)
		if checkout_time and early_exit_threshold and status != "Half Day":
			checkout_only_time = get_datetime(checkout_time).time()
			if checkout_only_time < early_exit_threshold:
				# Check if should treat as half day
				current_month_late_count = get_month_late_count(employee, date)
				if current_month_late_count >= treat_late_as_half_day_after:
					status = "Half Day"
					remarks_list.append(
						"Early exit treated as Half Day (exceeded {0} late marks)".format(
							treat_late_as_half_day_after
						)
					)
				else:
					early_exit = True
					remarks_list.append("Early exit before {0}".format(early_exit_threshold))
		
		# Check if missing logs should be treated as half day
		if (late_entry or early_exit) and status == "Present":
			current_month_late_count = get_month_late_count(employee, date)
			if current_month_late_count >= treat_late_as_half_day_after:
				status = "Half Day"
				late_entry = False
				early_exit = False
				remarks_list.append(
					"Missing log treated as Half Day (exceeded {0} late marks)".format(
						treat_late_as_half_day_after
					)
				)
		
	except Exception as e:
		frappe.log_error(
			title="Determine Status Error: {0}".format(employee),
			message=frappe.get_traceback()
		)
	
	remarks = ", ".join(remarks_list) if remarks_list else "Regular attendance"
	return status, late_entry, early_exit, remarks


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


def create_attendance_record(employee, date, status, late_entry, early_exit, working_hours, remarks):
	"""Create and submit attendance record"""
	try:
		attendance = frappe.get_doc({
			"doctype": "Attendance",
			"employee": employee,
			"attendance_date": date,
			"status": status,
			"remarks": remarks
		})
		
		# Set late_entry and early_exit if fields exist
		if hasattr(attendance, 'late_entry'):
			attendance.late_entry = 1 if late_entry else 0
		if hasattr(attendance, 'early_exit'):
			attendance.early_exit = 1 if early_exit else 0
		if hasattr(attendance, 'working_hours'):
			attendance.working_hours = working_hours
		
		attendance.insert(ignore_permissions=True)
		attendance.submit()
		frappe.db.commit()
		
	except Exception as e:
		frappe.log_error(
			title="Create Attendance Error: {0} - {1}".format(employee, date),
			message=frappe.get_traceback()
		)
		raise


def is_holiday_for_company(date):
	"""Check if date is a holiday based on Global Defaults company holiday list"""
	try:
		from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday as check_holiday
		
		# Get default company from Global Defaults
		default_company = frappe.db.get_single_value("Global Defaults", "default_company")
		if not default_company:
			# No default company set, skip holiday check
			return False
		
		# Get company's default holiday list
		holiday_list = frappe.get_cached_value("Company", default_company, "default_holiday_list")
		if not holiday_list:
			# No holiday list defined, skip holiday check
			return False
		
		return check_holiday(holiday_list, date)
	except:
		pass
	
	return False
