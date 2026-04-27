# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, add_days, getdate, get_first_day, get_last_day
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_to_remote_erp
from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on

class OTPLLeave(Document):
	def validate(self):
		"""
		Validate OTPL Leave before saving
		"""
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
			# Calculate total number of days
			if self.from_date and self.to_date:
				self.total_no_of_days = date_diff(self.to_date, self.from_date) + 1
				if self.half_day:
					self.total_no_of_days -= 0.5
			if self.approved_from_date and self.approved_to_date:
				self.total_no_of_approved_days = date_diff(self.approved_to_date, self.approved_from_date) + 1
				if self.half_day:
					self.total_no_of_approved_days -= 0.5

		# Validate status change to Cancelled
		if not self.get("__islocal"):
			doc_before_save = self.get_doc_before_save()
			if doc_before_save and doc_before_save.status != "Cancelled" and self.status == "Cancelled":
				# Only allow Cancelled status if previously Approved
				if doc_before_save.status != "Approved":
					frappe.throw("OTPL Leave can only be cancelled if it is Approved", title="Invalid Status Change")

		# Add any necessary validation logic here
		employee_doc = frappe.get_doc("Employee", self.employee)
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
			if not business_line_doc.reporting_manager or not business_line_doc.external_reporting_manager:
				frappe.throw("Business Line does not have a Reporting Manager assigned. Please contact HR.")
			if business_line_doc.reporting_manager:
				report_to = business_line_doc.reporting_manager
				# If the business line reporting manager is on leave on this leave's from_date,
				# escalate to that manager's reports_to.
				if self._is_on_leave(report_to):
					manager_report_to = frappe.db.get_value("Employee", report_to, "reports_to")
					if manager_report_to:
						report_to = manager_report_to
				user = frappe.db.get_value("Employee", report_to, "user_id")
				if user:
					self.approver = user
					self.is_external_manager = 0
					self.external_manager = ""
			if business_line_doc.external_reporting_manager:
				self.is_external_manager = 1
				self.external_manager = business_line_doc.external_reporting_manager
				self.approver = ""
		elif employee_doc.external_reporting_manager == 1:
			external_report_to = employee_doc.external_report_to
			report_to = frappe.db.get_value("Employee Pull", external_report_to, "employee")
			self.is_external_manager = 1
			self.external_manager = report_to
			self.approver = ""
		else:
			if employee_doc.reports_to:
				report_to = employee_doc.reports_to
				# If the reports_to manager is on leave on this leave's from_date,
				# escalate to that manager's reports_to.
				if self._is_on_leave(report_to):
					manager_doc = frappe.get_doc("Employee", report_to)
					if manager_doc.reports_to:
						report_to = manager_doc.reports_to
				user = frappe.db.get_value("Employee", report_to, "user_id")
				if user:
					self.approver = user
					self.is_external_manager = 0
					self.external_manager = ""
		if employee_doc.location == "Site" and employee_doc.staff_type == "Worker":
			if self.short_leave == 1 or self.half_day == 1:
				frappe.throw("Short Leave or Half Day not allowed")

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
		# Push to remote ERP if external manager is set
		push_leave_to_remote_erp(self)
		self.create_leave_applications()

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
				if self.short_leave:
					self._create_short_leave_application()
				else:
					self._create_regular_leave_applications()

	def _create_short_leave_application(self):
		"""Create a Short Leave application - does not consume CL or LWP"""
		from_date = self.approved_from_date or self.from_date
		short_leave_type = "Short Leave"

		self.make_leave_application(
			leave_type=short_leave_type,
			from_date=from_date,
			to_date=from_date,
			total_days=1,
			half_day=0
		)

	def _create_regular_leave_applications(self):
		from_date = self.approved_from_date
		to_date = self.approved_to_date

		if not (from_date and to_date):
			frappe.throw("Approved From Date and Approved To Date are required")

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