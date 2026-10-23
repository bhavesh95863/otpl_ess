# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Attendance reminder schedule for the ESS app.

The app queues local check-in / check-out alarms a week ahead. This module tells
it, per calendar day, whether to remind and at what shift times, so the times
come from ERPNext instead of being hardcoded per branch in the app.

Each day is decided by the first rule that matches:
  1. Holiday List row          -> "Weekly Off" / "Holiday"
  2. Approved full-day leave   -> "On Leave"  (OTPL Leave, Leave Application,
                                  On Leave Attendance; half day and short leave
                                  still remind)
  3. Shift                     -> the ESS Location shift of the employee's
                                  location (what daily attendance enforces)
     nothing found             -> "No Shift"

The shift is the same every day: it comes from the location on the Employee
master. Site and Field staff get no check-out reminder (the dashboard never
offers them a check-out either). Site has no ESS Location row, so its check-in
time comes from Employee Self Service Settings.

Every lookup covers the whole window in one query; nothing queries per day.

When an approved leave inside the window changes, the employee's devices get a
silent refresh push so the app re-fetches the schedule (see
on_otpl_leave_update / on_leave_application_change).
"""

import frappe
from frappe.utils import add_days, cint, get_time, getdate, now_datetime, nowdate

from employee_self_service.mobile.v1.api_utils import get_system_timezone

WINDOW_DAYS = 7
MAX_DAYS = 14
MAX_OFFSETS = 8
NO_CHECKOUT_LOCATIONS = ("Site",)
NO_CHECKOUT_STAFF_TYPES = ("Field",)
SETTINGS = "Employee Self Service Settings"
REFRESH_PUSH_TYPE = "attendance_reminder_refresh"
# Several leave events for one employee usually land together (an OTPL Leave
# approval also submits its Leave Applications): one push per employee per window.
REFRESH_PUSH_DEBOUNCE_SEC = 60


def build_reminder_schedule(employee, days=WINDOW_DAYS):
	"""Reminder schedule for `days` days from today, or None when the employee
	should get no reminders at all."""
	settings = frappe.db.get_singles_dict(SETTINGS)
	if cint(settings.get("disable_attendance_reminders")):
		return None

	emp = frappe.db.get_value(
		"Employee",
		employee,
		["name", "status", "location", "staff_type", "employee_availability"],
		as_dict=True,
	)
	if not emp or emp.status != "Active":
		return None

	start = getdate(nowdate())
	end = add_days(start, days - 1)

	holidays = _holidays(employee, start, end)
	leave_days = _full_leave_days(employee, start, end)
	# The dashboard blocks check-in while On Leave; today's entry must agree.
	if emp.employee_availability == "On Leave":
		leave_days.add(start)
	shift = _location_shift(emp.location, settings)
	no_checkout = (
		emp.location in NO_CHECKOUT_LOCATIONS or emp.staff_type in NO_CHECKOUT_STAFF_TYPES
	)

	schedule = []
	for i in range(days):
		date = add_days(start, i)
		reason = (
			holidays.get(date)
			or ("On Leave" if date in leave_days else None)
			or (None if shift else "No Shift")
		)
		if reason:
			schedule.append(_day(date, None, None, reason))
			continue
		schedule.append(_day(
			date,
			_hms(shift.start_time),
			None if no_checkout else _hms(shift.end_time),
			None,
		))

	data = {
		"enabled": 1,
		"timezone": get_system_timezone(),
		"shift_type": shift.name if shift else None,
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
		"schedule": schedule,
	}
	offsets = _reminder_offsets(settings.get("attendance_reminder_offsets"))
	if offsets:
		data["reminder_offsets_minutes"] = offsets
	return data


def _day(date, check_in, check_out, reason):
	return {
		"date": str(date),
		"remind": 0 if reason else 1,
		"check_in_time": check_in,
		"check_out_time": check_out,
		"skip_reason": reason,
	}


def _hms(value):
	return get_time(value).strftime("%H:%M:%S") if value not in (None, "") else None


def _reminder_offsets(value):
	"""'-5, 0, 5' -> [-5, 0, 5]; anything unparsable means "use the app default"."""
	if not value:
		return None
	try:
		offsets = [int(v) for v in str(value).replace(" ", "").split(",") if v != ""]
	except ValueError:
		return None
	return offsets[:MAX_OFFSETS] or None


def _holidays(employee, start, end):
	from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return {}
	# v12 Holiday rows carry no weekly_off flag: a weekly off is a row on the
	# list's weekly_off weekday (or one described by its weekday name, for lists
	# whose weekly_off was left blank).
	rows = frappe.db.sql(
		"""
		SELECT h.holiday_date, h.description, hl.weekly_off
		FROM `tabHoliday` h
		JOIN `tabHoliday List` hl ON hl.name = h.parent
		WHERE h.parent = %s AND h.holiday_date BETWEEN %s AND %s
		""",
		(holiday_list, start, end),
		as_dict=True,
	)
	result = {}
	for r in rows:
		date = getdate(r.holiday_date)
		weekday = date.strftime("%A")
		is_weekly_off = weekday == r.weekly_off or (r.description or "").strip() == weekday
		result[date] = "Weekly Off" if is_weekly_off else "Holiday"
	return result


def _full_leave_days(employee, start, end):
	"""Dates in the window the employee is away for the whole day."""
	days = set()

	# OTPL Leave is what the app applies. The approved range is what the manager
	# granted (an amendment cancels the leave and raises a new approved one).
	# Short leave is a within-day absence: the employee still checks in.
	otpl_leaves = frappe.db.sql(
		"""
		SELECT IFNULL(approved_from_date, from_date) AS from_date,
			IFNULL(approved_to_date, to_date) AS to_date,
			half_day, half_day_date
		FROM `tabOTPL Leave`
		WHERE employee = %(employee)s AND status = 'Approved' AND short_leave = 0
			AND IFNULL(approved_from_date, from_date) <= %(end)s
			AND IFNULL(approved_to_date, to_date) >= %(start)s
		""",
		{"employee": employee, "start": start, "end": end},
		as_dict=True,
	)
	# Leave Applications are created from approved OTPL Leaves, but HR also
	# books some directly.
	leave_applications = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"docstatus": 1,
			"status": "Approved",
			"from_date": ["<=", end],
			"to_date": [">=", start],
		},
		fields=["from_date", "to_date", "half_day", "half_day_date"],
	)
	for lv in otpl_leaves + leave_applications:
		frm, to = getdate(lv.from_date), getdate(lv.to_date)
		half = getdate(lv.half_day_date) if lv.half_day_date else (frm if frm == to else None)
		d = max(frm, start)
		while d <= min(to, end):
			if not (lv.half_day and d == half):
				days.add(d)
			d = add_days(d, 1)

	days.update(getdate(a.attendance_date) for a in frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"docstatus": 1,
			"status": "On Leave",
			"attendance_date": ["between", [start, end]],
		},
		fields=["attendance_date"],
	))
	return days


def _location_shift(location, settings):
	"""Shift times of the employee's location (Employee.location -> ESS Location).
	Site has no ESS Location row, so it falls back to the Site check-in time in
	Employee Self Service Settings."""
	if not location:
		return None
	row = frappe.db.get_value(
		"ESS Location", location, ["name", "shift_start_time", "shift_end_time"], as_dict=True
	)
	if row and row.shift_start_time:
		return frappe._dict(name=row.name, start_time=row.shift_start_time, end_time=row.shift_end_time)
	if location in NO_CHECKOUT_LOCATIONS and settings.get("site_check_in_reminder_time"):
		return frappe._dict(name=location, start_time=settings.get("site_check_in_reminder_time"), end_time=None)
	return None


# ---------------------------------------------------------------------------
# Silent refresh push
# ---------------------------------------------------------------------------

def on_otpl_leave_update(doc, method=None):
	"""doc_event for OTPL Leave on_update: an approval, a cancellation or a change
	to an approved leave's dates (amendments cancel the original and approve a new
	leave) inside the window refreshes the employee's alarms."""
	before = doc.get_doc_before_save()
	was_approved = bool(before) and before.status == "Approved"
	is_approved = doc.status == "Approved"

	ranges = []
	if is_approved:
		ranges.append(_otpl_range(doc))
	if was_approved:
		ranges.append(_otpl_range(before))
	if not ranges:
		return
	if was_approved and is_approved and ranges[0] == ranges[1] and \
			(before.half_day, before.short_leave) == (doc.half_day, doc.short_leave):
		return
	if any(_touches_window(frm, to) for frm, to in ranges):
		enqueue_refresh_push(doc.employee)


def on_leave_application_change(doc, method=None):
	"""doc_event for Leave Application submit / cancel (covers leave HR books
	directly; ones raised from an OTPL Leave are debounced with it). A rejected
	leave never blocked a day; on cancel the status is already Cancelled."""
	if doc.status != "Rejected" and _touches_window(doc.from_date, doc.to_date):
		enqueue_refresh_push(doc.employee)


def enqueue_refresh_push(employee):
	if not employee or not cint(frappe.db.get_single_value(SETTINGS, "enable_reminder_refresh_push")):
		return
	frappe.enqueue(
		"employee_self_service.employee_self_service.utils.attendance_reminder.send_refresh_push",
		employee=employee,
		queue="short",
		job_name="ess_reminder_refresh::{0}".format(employee),
		enqueue_after_commit=True,  # never push for a change that rolls back
	)


def send_refresh_push(employee):
	# Atomic SET NX: the first job in the debounce window sends, the rest skip.
	cache = frappe.cache()
	cache_key = cache.make_key("ess_reminder_refresh_push::{0}".format(employee))
	if not cache.set(cache_key, 1, ex=REFRESH_PUSH_DEBOUNCE_SEC, nx=True):
		return

	user = frappe.db.get_value("Employee", employee, "user_id")
	if not user:
		return
	tokens = {
		d.token for d in frappe.get_all(
			"Employee Device Info", filters={"user": user, "token": ["is", "set"]}, fields=["token"]
		)
	}
	for token in filter(None, tokens):
		send_fcm_data_message(token, {"type": REFRESH_PUSH_TYPE})


def send_fcm_data_message(token, data):
	"""Data-only push through the Nesscale notification relay. The relay must send
	it with no notification block (and APNs background headers), otherwise the
	phone would show it; enable_reminder_refresh_push stays off until it does."""
	import json
	import requests

	from employee_self_service.employee_self_service.doctype.ess_notification_log.ess_notification_log import (
		NOTIFICATION_RELAY_URL,
	)

	payload = {
		"product_name": "OTPL ESS",
		"tokens": [token],
		"erp_url": frappe.utils.get_url(),
		"data_only": 1,
		"data": data,
	}
	try:
		response = requests.post(
			NOTIFICATION_RELAY_URL,
			headers={"Content-Type": "application/json"},
			data=json.dumps(payload),
			timeout=10,
		)
		if response.status_code != 200:
			frappe.log_error(
				title="ESS Reminder Refresh Push Error",
				message="Status Code: {0}, Response: {1}".format(response.status_code, response.text),
			)
	except Exception:
		frappe.log_error(title="ESS Reminder Refresh Push Error", message=frappe.get_traceback())


def _otpl_range(doc):
	return (
		getdate(doc.approved_from_date or doc.from_date),
		getdate(doc.approved_to_date or doc.to_date or doc.from_date),
	)


def _touches_window(from_date, to_date=None):
	if not from_date:
		return False
	start = getdate(nowdate())
	end = add_days(start, WINDOW_DAYS - 1)
	return getdate(from_date) <= end and getdate(to_date or from_date) >= start
