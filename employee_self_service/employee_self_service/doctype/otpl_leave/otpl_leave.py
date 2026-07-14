# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, add_days, getdate, get_first_day, get_last_day, nowdate
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_to_remote_erp
from employee_self_service.employee_self_service.utils.leave_escalation import (
	resolve_external_manager_pull,
	resolve_approver_chain,
	get_employee_contact,
	get_employee_pull_contact,
)
from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on

class OTPLLeave(Document):
	def validate(self):
		"""
		Validate OTPL Leave before saving
		"""
		# "From Date must be today or later" — enforced only on first save
		# (creation), so editing/approving an existing leave whose from_date has
		# since passed is not blocked.
		if self.is_new() and self.from_date:
			if getdate(self.from_date) < getdate(nowdate()):
				frappe.throw("Leave From Date cannot be in the past. Please select today or a later date.")

		# Short leave must be single day, no half day
		if self.short_leave:
			self.half_day = 0
			self.to_date = self.from_date
			self.total_no_of_days = 1
			if self.approved_from_date:
				self.approved_to_date = self.approved_from_date
				self.total_no_of_approved_days = 1
			self.validate_short_leave_limit()
		else:
			if self.half_day:
				self.validate_half_day()

			# Calculate total number of days
			if self.from_date and self.to_date:
				self.total_no_of_days = date_diff(self.to_date, self.from_date) + 1
				if self.half_day:
					self.total_no_of_days -= 0.5
			if self.approved_from_date and self.approved_to_date:
				self.total_no_of_approved_days = date_diff(self.approved_to_date, self.approved_from_date) + 1
				if self.half_day:
					self.total_no_of_approved_days -= 0.5

		# A half day that was merged into a full-day leave is final. Re-opening it
		# would leave the date with BOTH a full-day Leave Application and a live
		# half day — a double-counted day. The whole day's leave is withdrawn by
		# cancelling the full-day leave it was merged into, not this record.
		if self.merged_into and self.status != "Cancelled":
			frappe.throw(
				"This half day was combined with the other half day on the same date "
				"into a single full-day leave ({0}), so it cannot be re-opened. "
				"To withdraw the day's leave, cancel {0} instead.".format(self.merged_into),
				title="Merged Half Day"
			)

		# Validate status change to Cancelled
		if not self.get("__islocal"):
			doc_before_save = self.get_doc_before_save()
			if doc_before_save and doc_before_save.status != "Cancelled" and self.status == "Cancelled":
				# Only allow Cancelled status if previously Approved
				if doc_before_save.status != "Approved":
					frappe.throw("OTPL Leave can only be cancelled if it is Approved", title="Invalid Status Change")

		# Add any necessary validation logic here
		employee_doc = frappe.get_doc("Employee", self.employee)

		# Track approver contact details (name + mobile) as we determine the approver.
		# Cannot use fetch_from since approver may be either an internal User
		# or an external Employee Pull record.
		approver_name = None
		approver_mobile = None

		if (
			employee_doc.staff_type == "Worker"
			and employee_doc.location == "Site"
			and not employee_doc.is_team_leader
		):
			# Worker on site (not a team leader): approval goes to the
			# Business Line Manager defined on the Business Line, same as Travel Request.
			business_vertical = employee_doc.business_vertical or employee_doc.external_business_vertical
			if not business_vertical:
				frappe.throw("Employee does not have a Business Vertical assigned. Please contact HR.")
			business_line_doc = frappe.get_doc("Business Line", business_vertical)
			if not business_line_doc.reporting_manager and not business_line_doc.external_reporting_manager:
				frappe.throw("Business Line does not have a Reporting Manager assigned. Please contact HR.")

			# Reset before resolving — chain may transition between internal/external.
			self.approver = ""
			self.is_external_manager = 0
			self.external_manager = ""

			if business_line_doc.reporting_manager:
				resolved = resolve_approver_chain(
					{"type": "internal", "employee": business_line_doc.reporting_manager},
					on_date=self.from_date,
					strict=True,
				)
				if resolved:
					if resolved.get("type") == "internal":
						report_to = resolved.get("employee")
						user = frappe.db.get_value("Employee", report_to, "user_id")
						if user:
							self.approver = user
							approver_name, approver_mobile = get_employee_contact(report_to)
					else:
						pull = resolved.get("pull") or {}
						self.is_external_manager = 1
						self.external_manager = pull.get("employee")
						approver_name = pull.get("employee_name")
						approver_mobile = pull.get("mobile_no")

			if business_line_doc.external_reporting_manager and not self.is_external_manager and not self.approver:
				resolved = resolve_approver_chain(
					{"type": "external", "pull_name": business_line_doc.external_reporting_manager},
					on_date=self.from_date,
					strict=True,
				)
				if resolved:
					if resolved.get("type") == "external":
						pull = resolved.get("pull") or {}
						self.is_external_manager = 1
						self.external_manager = pull.get("employee")
						approver_name = pull.get("employee_name")
						approver_mobile = pull.get("mobile_no")
					else:
						report_to = resolved.get("employee")
						user = frappe.db.get_value("Employee", report_to, "user_id")
						if user:
							self.approver = user
							approver_name, approver_mobile = get_employee_contact(report_to)
		elif employee_doc.external_reporting_manager == 1:
			resolved = resolve_approver_chain(
				{"type": "external", "pull_name": employee_doc.external_report_to},
				on_date=self.from_date,
				strict=True,
			)
			if resolved and resolved.get("type") == "external":
				pull = resolved.get("pull") or {}
				self.is_external_manager = 1
				self.external_manager = pull.get("employee")
				self.approver = ""
				approver_name = pull.get("employee_name")
				approver_mobile = pull.get("mobile_no")
			elif resolved and resolved.get("type") == "internal":
				report_to = resolved.get("employee")
				user = frappe.db.get_value("Employee", report_to, "user_id")
				if user:
					self.approver = user
					self.is_external_manager = 0
					self.external_manager = ""
					approver_name, approver_mobile = get_employee_contact(report_to)
		else:
			if employee_doc.reports_to:
				resolved = resolve_approver_chain(
					{"type": "internal", "employee": employee_doc.reports_to},
					on_date=self.from_date,
					strict=True,
				)
				if resolved and resolved.get("type") == "internal":
					report_to = resolved.get("employee")
					user = frappe.db.get_value("Employee", report_to, "user_id")
					if user:
						self.approver = user
						self.is_external_manager = 0
						self.external_manager = ""
						approver_name, approver_mobile = get_employee_contact(report_to)
				elif resolved and resolved.get("type") == "external":
					pull = resolved.get("pull") or {}
					self.is_external_manager = 1
					self.external_manager = pull.get("employee")
					self.approver = ""
					approver_name = pull.get("employee_name")
					approver_mobile = pull.get("mobile_no")

		# All branches must produce an approver. If the escalation chain found
		# no available manager (everyone on leave), block the save with a
		# clear error so HR can fix the chain.
		if not self.approver and not self.is_external_manager:
			frappe.throw(
				"No available approver found — all managers in the escalation chain are on leave. Please contact HR.",
				title="No Approver Available",
			)

		# Applicant contact + Approver contact
		applicant_name, applicant_mobile = get_employee_contact(self.employee)
		if applicant_name and not self.employee_name:
			self.employee_name = applicant_name
		self.applicant_mobile_no = applicant_mobile or ""
		self.approver_name = approver_name or ""
		self.approver_mobile_no = approver_mobile or ""

		if employee_doc.location == "Site" and employee_doc.staff_type == "Worker":
			if self.short_leave == 1 or self.half_day == 1:
				frappe.throw("Short Leave or Half Day not allowed")

	def validate_half_day(self):
		"""Validate a Half Day leave.

		A Half Day leave no longer creates a Leave Application for the half day
		itself — daily attendance processes that day for real (Half Day status,
		plus late / early marks against leave-shifted thresholds). For that to
		work two things must hold:

		1. half_day_period must be known, so attendance can tell which side of
		   the shift the leave falls on. The mobile app has sent this reliably
		   since Mar-2026; older records may have it blank, so it is only
		   enforced on new records and at approval time (a blank one can still
		   be cancelled).
		2. The half day must sit at the FIRST or LAST day of the leave. The rest
		   of the range still becomes a Leave Application, and carving the half
		   day out of an edge always leaves one contiguous block — a half day in
		   the middle would split the range in two.

		The date range is never rewritten here: a Half Day may legitimately be
		one end of a multi-day leave (e.g. half day Fri + full day Sat).
		"""
		if self.status == "Cancelled":
			return

		if not self.half_day_date:
			self.half_day_date = self.from_date

		if self.from_date and self.to_date:
			half_day_date = getdate(self.half_day_date)
			if half_day_date not in (getdate(self.from_date), getdate(self.to_date)):
				frappe.throw(
					"The Half Day must be the first or the last day of the leave "
					"({0} to {1}), but it is set to {2}.".format(
						self.from_date, self.to_date, self.half_day_date
					),
					title="Invalid Half Day Date"
				)

		if not self.half_day_period and (self.is_new() or self.status == "Approved"):
			frappe.throw(
				"Please select the Half Day Period (First Half or Second Half). "
				"Attendance needs it to know when the employee is due in or out.",
				title="Half Day Period Required"
			)

	def validate_short_leave_limit(self):
		"""Validate that employee has not exceeded 2 short leaves in the month"""
		if self.status == "Cancelled":
			return

		leave_date = getdate(self.from_date)
		month_start = str(get_first_day(leave_date))
		month_end = str(get_last_day(leave_date))

		query = """
			SELECT COUNT(*) FROM `tabOTPL Leave`
			WHERE employee = %s
			AND short_leave = 1
			AND from_date BETWEEN %s AND %s
			AND status IN ('Pending', 'Approved')
		"""
		params = [self.employee, month_start, month_end]

		if not self.get("__islocal") and self.name:
			query += " AND name != %s"
			params.append(self.name)

		existing_count = frappe.db.sql(query, params)[0][0] or 0
		if existing_count >= 2:
			frappe.throw(
				f"Maximum 2 Short Leaves are allowed per month. "
				f"Employee already has {existing_count} Short Leave(s) in {leave_date.strftime('%B %Y')}.",
				title="Short Leave Limit Exceeded"
			)

	def _is_on_leave(self, employee):
		"""Check whether the given employee has an approved Leave Application
		covering this OTPL Leave's from_date."""
		if not employee or not self.from_date:
			return False
		return bool(
			frappe.db.exists(
				"Leave Application",
				{
					"employee": employee,
					"status": "Approved",
					"docstatus": 1,
					"from_date": ["<=", self.from_date],
					"to_date": [">=", self.from_date],
				},
			)
		)

	def on_update(self):
		"""
		Trigger sync to remote ERP when leave is saved with external manager
		"""
		# Create Leave Applications FIRST, before anything commits.
		# This must run before push_leave_to_remote_erp(), which calls
		# frappe.db.commit() internally: if creation were done after that commit
		# and then failed, the OTPL Leave would already be persisted as "Approved"
		# with no Leave Application. By creating first, any failure here raises
		# before a commit happens, so Frappe rolls back the whole save (including
		# the status change) and the leave is not left approved without an application.
		self.create_leave_applications()

		# Push to remote ERP if external manager is set
		push_leave_to_remote_erp(self)

		# Handle status change to Cancelled
		if not self.get("__islocal"):
			doc_before_save = self.get_doc_before_save()
			if doc_before_save and doc_before_save.status != "Cancelled" and self.status == "Cancelled":
				self.cancel_linked_leave_applications()

	def create_leave_applications(self):
		if not self.get("__islocal"):
			doc_before_save = self.get_doc_before_save()
			if (
				doc_before_save
				and doc_before_save.status != "Approved"
				and self.status == "Approved"
			):
				# Short Leave is a within-day absence: the employee is still
				# present for the day, so it must NOT create a Leave Application
				# (which would generate "On Leave"/"Half Day" attendance and
				# overwrite the actual Present attendance). Short Leave is tracked
				# on the OTPL Leave only — daily attendance adjusts its thresholds
				# (get_approved_short_leave_period) and payroll counts it directly.
				#
				# A Half Day is handled the same way, but only for the half day
				# ITSELF: _create_regular_leave_applications carves half_day_date
				# out of the range, so any remaining full-leave days still get a
				# Leave Application. A single-day Half Day therefore creates none.
				if self.short_leave:
					return

				# Two approved half days on the SAME date add up to a whole day off.
				# Collapse the pair into this single full-day leave before any Leave
				# Application is built, so the day becomes ordinary full-day leave:
				# one Leave Application consuming a full day of CL, "On Leave"
				# attendance, and no half-day timing rules.
				if self.half_day:
					self._merge_opposite_half_day()

				self._create_regular_leave_applications()

	def _find_opposite_half_day(self):
		"""The employee's other approved half-day leave on the same date, if any.

		Returns the OTPL Leave name whose half_day_period is the OPPOSITE half of
		this one (First Half vs Second Half) on the same half_day_date. Periods are
		normalised before comparison because the mobile app stores this field
		already translated for some locales (e.g. 'पहली छमाही').
		"""
		from employee_self_service.employee_self_service.utils.daily_attendance import (
			normalize_half_day_period,
		)

		period = normalize_half_day_period(self.half_day_period)
		if not (period and self.half_day_date):
			return None

		opposite = "Second Half" if period == "First Half" else "First Half"

		# Merging rewrites this record into a single full-day leave, so it is only
		# safe when BOTH leaves cover just that one day. A half day can also be the
		# first/last day of a longer leave (see _leave_application_range); collapsing
		# such a leave to one date would silently destroy its full-leave days. Those
		# stay unmerged — OTPL Payroll still counts the paired date as one full leave
		# day, so the money is right either way.
		if getdate(self.from_date) != getdate(self.to_date):
			return None

		for row in frappe.get_all(
			"OTPL Leave",
			filters={
				"name": ["!=", self.name],
				"employee": self.employee,
				"half_day": 1,
				"status": "Approved",
				"half_day_date": self.half_day_date,
			},
			fields=["name", "half_day_period", "from_date", "to_date"],
		):
			if normalize_half_day_period(row.half_day_period) != opposite:
				continue
			if getdate(row.from_date) != getdate(row.to_date):
				continue
			return row.name

		return None

	def _merge_opposite_half_day(self):
		"""Collapse a First Half + Second Half pair on the same date into THIS leave.

		The employee filed two half days; together they are a full day away. This
		record becomes that full-day leave (half_day cleared, range pinned to the
		single date) and the other is Cancelled with `merged_into` pointing here.

		Doing it here — before _create_regular_leave_applications runs — means the
		full-day Leave Application falls out of the normal path with no special
		casing, and every downstream consumer (attendance -> "On Leave", payroll ->
		one full approved leave day consuming CL) sees an ordinary full-day leave.

		Writes go through db_set so validate() is not re-entered mid-approval.
		"""
		other = self._find_opposite_half_day()
		if not other:
			return

		merged_date = self.half_day_date

		# This record becomes the full-day leave for that date.
		self.half_day = 0
		self.half_day_period = None
		self.half_day_date = None
		self.from_date = merged_date
		self.to_date = merged_date
		self.approved_from_date = merged_date
		self.approved_to_date = merged_date
		self.total_no_of_days = 1
		self.total_no_of_approved_days = 1

		for field in (
			"half_day", "half_day_period", "half_day_date",
			"from_date", "to_date", "approved_from_date", "approved_to_date",
			"total_no_of_days", "total_no_of_approved_days",
		):
			self.db_set(field, self.get(field), update_modified=False)

		# Retire the other half. It never had a Leave Application of its own (a
		# half day does not create one), so there is nothing to cancel behind it.
		other_doc = frappe.get_doc("OTPL Leave", other)
		other_doc.flags.ignore_permissions = True
		other_doc.db_set("merged_into", self.name, update_modified=False)
		other_doc.db_set("status", "Cancelled", update_modified=False)

		note = "Merged into {0}: this half day and {1} fall on the same date ({2}), " \
			"so together they were approved as one full-day leave.".format(
				self.name, other, merged_date
			)
		self.add_comment("Comment", text=note)
		other_doc.add_comment("Comment", text=note)

		frappe.msgprint(
			"Two half-day leaves on {0} were combined into a single full-day leave. "
			"{1} has been cancelled and merged into {2}.".format(merged_date, other, self.name),
			title="Half Days Combined",
			indicator="orange",
		)

	def _uses_checkin_based_attendance(self):
		"""True when this employee's attendance is computed from check-in / check-out
		against ESS Location rules (daily_attendance.determine_status).

		Only those employees can have a Half Day measured by punch times, so they
		are the only ones for whom the half day is carved out of the Leave
		Application. Workers, Field staff, Noida Drivers and no-check-in employees
		are processed by other paths (run_worker_attendance,
		_process_field_attendance, run_driver_attendance, auto-Present) that know
		nothing about half-day timing — for them the Leave Application is what
		produces the Half Day attendance, so it must keep being created.
		"""
		row = frappe.db.get_value(
			"Employee", self.employee, ["staff_type", "location", "no_check_in"]
		)
		if not row:
			return False

		staff_type, location, no_check_in = row

		if no_check_in:
			return False
		if staff_type in ("Worker", "Field"):
			return False
		if staff_type == "Driver" and location == "Noida":
			return False
		return True

	def _leave_application_range(self, from_date, to_date):
		"""Narrow an approved range to the days that still need a Leave Application.

		For check-in-based employees the half day itself never gets one: it is
		tracked on the OTPL Leave and processed as real attendance (Half Day
		status + late / early marks), so it is carved out of the range here.
		validate_half_day() guarantees the half day is the first or last day of
		the leave, so removing it always leaves one contiguous block.

		Returns (None, None) when nothing but the half day remains — i.e. the
		ordinary single-day Half Day, which creates no Leave Application at all.
		"""
		if not (self.half_day and self.half_day_date):
			return from_date, to_date

		# Employees whose attendance is not punch-based keep the old behaviour:
		# the Leave Application covers the half day and generates its attendance.
		if not self._uses_checkin_based_attendance():
			return from_date, to_date

		half_day_date = getdate(self.half_day_date)
		start, end = getdate(from_date), getdate(to_date)

		# Half day outside the approved range: the approver trimmed it away, so
		# the whole approved range is ordinary full-day leave.
		if not (start <= half_day_date <= end):
			return from_date, to_date

		if start == end:
			return None, None
		if half_day_date == start:
			return add_days(from_date, 1), to_date
		if half_day_date == end:
			return from_date, add_days(to_date, -1)

		# Mid-range half day is blocked by validate_half_day(); if a legacy
		# record slips through, keep the old behaviour rather than lose days.
		return from_date, to_date

	def _create_regular_leave_applications(self):
		from_date = self.approved_from_date
		to_date = self.approved_to_date

		if not (from_date and to_date):
			frappe.throw("Approved From Date and Approved To Date are required")

		from_date, to_date = self._leave_application_range(from_date, to_date)
		if not (from_date and to_date):
			# Nothing left but the half day — no Leave Application to create.
			return

		calendar_days = date_diff(to_date, from_date) + 1
		casual_leave = "Casual Leave"
		lwp = "Leave Without Pay"

		cl_balance = get_leave_balance_on(
			employee=self.employee,
			leave_type=casual_leave,
			date=from_date,
			consider_all_leaves_in_the_allocation_period=True
		) or 0

		# Use integer calendar days for the CL/LWP split to keep date arithmetic safe
		cl_calendar_days = min(int(cl_balance), calendar_days)
		lwp_calendar_days = calendar_days - cl_calendar_days

		if cl_calendar_days > 0:
			cl_to_date = add_days(from_date, cl_calendar_days - 1)
			cl_leave_days = cl_calendar_days
			cl_half_day = 0
			cl_half_day_date = None
			if self.half_day and self._is_half_day_in_range(from_date, cl_to_date):
				cl_leave_days -= 0.5
				cl_half_day = 1
				cl_half_day_date = self.half_day_date
			self.make_leave_application(
				leave_type=casual_leave,
				from_date=from_date,
				to_date=cl_to_date,
				total_days=cl_leave_days,
				half_day=cl_half_day,
				half_day_date=cl_half_day_date
			)

		if lwp_calendar_days > 0:
			lwp_from_date = add_days(from_date, cl_calendar_days)
			lwp_leave_days = lwp_calendar_days
			lwp_half_day = 0
			lwp_half_day_date = None
			if self.half_day and self._is_half_day_in_range(lwp_from_date, to_date):
				lwp_leave_days -= 0.5
				lwp_half_day = 1
				lwp_half_day_date = self.half_day_date
			self.make_leave_application(
				leave_type=lwp,
				from_date=lwp_from_date,
				to_date=to_date,
				total_days=lwp_leave_days,
				half_day=lwp_half_day,
				half_day_date=lwp_half_day_date
			)

	def _is_half_day_in_range(self, start_date, end_date):
		from frappe.utils import getdate
		if self.half_day_date:
			return getdate(start_date) <= getdate(self.half_day_date) <= getdate(end_date)
		# If no specific half_day_date, assume it applies (e.g. single day case)
		return True

	def make_leave_application(self, leave_type, from_date, to_date, total_days, half_day=0, half_day_date=None):
		company = frappe.db.get_value("Employee", self.employee, "company")
		leave_app = frappe.new_doc("Leave Application")
		leave_app.employee = self.employee
		leave_app.leave_type = leave_type
		leave_app.from_date = from_date
		leave_app.to_date = to_date
		leave_app.total_leave_days = total_days
		leave_app.description = f"Auto-created from OTPL Leave: {self.name}"
		leave_app.status = "Approved"
		leave_app.company = company
		leave_app.half_day = half_day
		if half_day and half_day_date:
			leave_app.half_day_date = half_day_date

		leave_app.insert(ignore_permissions=True)
		leave_app.submit()

		# Store reference to created leave application
		self.add_leave_application_reference(leave_app.name)

	def add_leave_application_reference(self, leave_app_name):
		"""Add leave application reference to the list"""
		existing_refs = self.leave_applications or ""
		refs_list = [ref.strip() for ref in existing_refs.split(",") if ref.strip()]

		if leave_app_name not in refs_list:
			refs_list.append(leave_app_name)
			self.leave_applications = ", ".join(refs_list)
			self.db_set("leave_applications", self.leave_applications, update_modified=False)

	def before_trash(self):
		"""Validate before deletion - only allow if status is Cancelled"""
		if self.status != "Cancelled":
			frappe.throw(
				"OTPL Leave can only be deleted if its status is 'Cancelled'. Please change the status to 'Cancelled' first.",
				title="Cannot Delete"
			)

	def on_trash(self):
		"""Delete all linked Leave Applications when OTPL Leave is deleted"""
		self.delete_linked_leave_applications()

	def on_cancel(self):
		"""Cancel all linked Leave Applications when OTPL Leave is cancelled"""
		self.cancel_linked_leave_applications()

	def cancel_linked_leave_applications(self):
		"""Cancel all Leave Applications linked to this OTPL Leave"""
		if not self.leave_applications:
			return

		refs_list = [ref.strip() for ref in self.leave_applications.split(",") if ref.strip()]

		for leave_app_name in refs_list:
			try:
				if frappe.db.exists("Leave Application", leave_app_name):
					leave_app = frappe.get_doc("Leave Application", leave_app_name)
					if leave_app.docstatus == 1:  # If submitted
						leave_app.flags.ignore_permissions = True
						leave_app.cancel()
						frappe.msgprint(f"Cancelled Leave Application: {leave_app_name}")
			except Exception as e:
				frappe.log_error(f"Error cancelling Leave Application {leave_app_name}: {str(e)}")
				frappe.msgprint(f"Warning: Could not cancel Leave Application {leave_app_name}", alert=True)

	def delete_linked_leave_applications(self):
		"""Delete all Leave Applications linked to this OTPL Leave"""
		if not self.leave_applications:
			return

		refs_list = [ref.strip() for ref in self.leave_applications.split(",") if ref.strip()]

		for leave_app_name in refs_list:
			try:
				if frappe.db.exists("Leave Application", leave_app_name):
					leave_app = frappe.get_doc("Leave Application", leave_app_name)
					# Cancel first if submitted
					if leave_app.docstatus == 1:
						leave_app.flags.ignore_permissions = True
						leave_app.cancel()
					# Then delete
					leave_app.flags.ignore_permissions = True
					frappe.delete_doc("Leave Application", leave_app_name, ignore_permissions=True, force=True)
					frappe.msgprint(f"Deleted Leave Application: {leave_app_name}")
			except Exception as e:
				frappe.log_error(f"Error deleting Leave Application {leave_app_name}: {str(e)}")
				frappe.msgprint(f"Warning: Could not delete Leave Application {leave_app_name}", alert=True)


@frappe.whitelist()
def bulk_cancel_otpl_leaves(names):
	"""Bulk cancel OTPL Leaves from list view"""
	if isinstance(names, str):
		import json
		names = json.loads(names)

	for name in names:
		doc = frappe.get_doc("OTPL Leave", name)
		if doc.status != "Approved":
			frappe.throw(f"OTPL Leave {name} is not Approved. Only Approved leaves can be cancelled.")
		doc.status = "Cancelled"
		doc.save(ignore_permissions=True)

	frappe.db.commit()


def validate_leave_application_cancel(doc, method):
	"""
	Prevent direct cancellation of Leave Applications created from OTPL Leave.
	Users must change OTPL Leave status to Cancelled instead.
	"""
	# Deliberate detach by a maintenance script (e.g. the half-day Leave
	# Application cleanup), which removes the Leave Application while the OTPL
	# Leave itself stays Approved.
	if doc.flags.get("ignore_otpl_leave_link"):
		return

	# Check if this Leave Application was created from OTPL Leave
	if doc.description and "Auto-created from OTPL Leave:" in doc.description:
		# Extract OTPL Leave name from description
		otpl_leave_name = doc.description.split("Auto-created from OTPL Leave:")[-1].strip()

		# Check if OTPL Leave exists and its status
		if frappe.db.exists("OTPL Leave", otpl_leave_name):
			otpl_leave_status = frappe.db.get_value("OTPL Leave", otpl_leave_name, "status")

			# Prevent direct cancellation unless OTPL Leave is being cancelled or deleted
			if otpl_leave_status not in ["Cancelled"]:
				frappe.throw(
					f"This Leave Application was created from OTPL Leave: {otpl_leave_name}. "
					f"Please change the OTPL Leave status to 'Cancelled', which will automatically cancel all linked Leave Applications.",
					title="Cannot Cancel Directly"
				)