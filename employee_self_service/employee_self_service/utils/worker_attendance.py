# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, get_datetime, now_datetime, time_diff_in_hours, add_days
from datetime import datetime, time

def validate_worker_checkin(employee, log_type, checkin_time=None):
	"""
	Validate check-in/check-out for Worker staff type with location != "Site"
	Returns: (is_valid, message, adjusted_time)
	"""
	# Get employee details
	emp_doc = frappe.get_doc("Employee", employee)
	staff_type = emp_doc.staff_type
	location = emp_doc.location
	
	# Only apply validations for Workers with location other than "Site"
	if staff_type != "Worker" or location == "Site":
		return True, "", checkin_time
	
	if not checkin_time:
		checkin_time = now_datetime()
	
	checkin_datetime = get_datetime(checkin_time)
	checkin_date = getdate(checkin_time)
	checkin_time_only = checkin_datetime.time()
	
	# Get ESS Location settings
	location_settings = None
	if location and frappe.db.exists("ESS Location", location):
		location_settings = frappe.get_doc("ESS Location", location)
	
	# If no location settings, allow check-in/out
	if not location_settings:
		return True, "", checkin_time
	
	# Get shift timings from location
	shift_start = location_settings.shift_start_time
	shift_end = location_settings.shift_end_time
	
	# Check if it's a holiday
	if is_holiday_for_employee(employee, checkin_date):
		# Check Allowed Overtime for holiday check-in
		allowed_overtime = get_allowed_overtime(employee, checkin_date)
		
		if not allowed_overtime or allowed_overtime.overtime_allowed != "Yes":
			return False, "You are on leave today", None
	
	# Check for early check-in or late check-out
	if log_type == "IN":
		# Check if checking in early
		if checkin_time_only < shift_start:
			allowed_overtime = get_allowed_overtime(employee, checkin_date)
			
			if not allowed_overtime or allowed_overtime.early_entry_allowed != "Yes":
				# Adjust check-in time to shift start
				adjusted_datetime = datetime.combine(checkin_date, shift_start)
				return True, "Early check in not allowed, checkin recorded at {0}".format(
					shift_start.strftime("%I:%M %p")
				), adjusted_datetime
	
	elif log_type == "OUT":
		# Check if checking out late
		if checkin_time_only > shift_end:
			allowed_overtime = get_allowed_overtime(employee, checkin_date)
			
			if not allowed_overtime or allowed_overtime.late_exit_allowed != "Yes":
				# Adjust check-out time to shift end
				adjusted_datetime = datetime.combine(checkin_date, shift_end)
				return True, "Late check out is not allowed, check out recorded at {0}".format(
					shift_end.strftime("%I:%M %p")
				), adjusted_datetime
	
	return True, "", checkin_time


def get_allowed_overtime(employee, date):
	"""Get Allowed Overtime entry for employee on given date"""
	allowed_overtime = frappe.db.get_value(
		"Allowed Overtime",
		{
			"employee": employee,
			"date": date
		},
		["name", "overtime_allowed", "early_entry_allowed", "late_exit_allowed"],
		as_dict=True
	)
	return allowed_overtime


def is_holiday_for_employee(employee, date):
	"""Check if date is a holiday or weekly off for employee"""
	try:
		from erpnext.hr.doctype.holiday_list.holiday_list import is_holiday as check_holiday
		
		emp = frappe.get_doc("Employee", employee)
		if emp.holiday_list:
			return check_holiday(emp.holiday_list, date)
	except:
		pass
	
	return False


def process_worker_attendance_with_hours(employee, location, date):
	"""
	Special attendance processing for Workers (location != Site)
	- If both check-in and check-out exist: Mark Present with hours
	- If only check-in exists (after auto-checkout): Mark Absent
	"""
	# Get employee details
	emp_doc = frappe.get_doc("Employee", employee)
	staff_type = emp_doc.staff_type
	
	# Only apply for Workers with location != Site
	if staff_type != "Worker" or location == "Site":
		return None  # Use standard attendance logic
	
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
	
	# Worker-specific logic
	if checkin_time and checkout_time:
		# Both check-in and check-out exist: Mark Present with hours
		working_hours = 0
		try:
			working_hours = time_diff_in_hours(
				get_datetime(checkout_time),
				get_datetime(checkin_time)
			)
			if working_hours < 0:
				working_hours = 0
		except:
			working_hours = 0
		
		# Create attendance record as Present
		from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record
		
		create_attendance_record(
			employee=employee,
			date=date,
			status="Present",
			late_entry=False,
			early_exit=False,
			working_hours=working_hours,
			remarks="Worker attendance with {0} hours".format(round(working_hours, 2))
		)
		return "Processed"
	
	elif checkin_time and not checkout_time:
		# Only check-in exists (no auto-checkout yet or missed): Mark Absent
		from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record
		
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker - Check-in only, no check-out recorded"
		)
		return "Absent"
	
	else:
		# No check-in: Mark Absent
		from employee_self_service.employee_self_service.utils.daily_attendance import create_attendance_record
		
		create_attendance_record(
			employee=employee,
			date=date,
			status="Absent",
			late_entry=False,
			early_exit=False,
			working_hours=0,
			remarks="Worker - No check-in recorded"
		)
		return "Absent"
