# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import date_diff,add_days
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_to_remote_erp
from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on

class OTPLLeave(Document):
	def validate(self):
		"""
		Validate OTPL Leave before saving
		"""
		# Calculate total number of days
		if self.from_date and self.to_date:
			self.total_no_of_days = date_diff(self.to_date, self.from_date) + 1

		# Validate status change to Cancelled
		if not self.get("__islocal"):
			doc_before_save = self.get_doc_before_save()
			if doc_before_save and doc_before_save.status != "Cancelled" and self.status == "Cancelled":
				# Only allow Cancelled status if previously Approved
				if doc_before_save.status != "Approved":
					frappe.throw("OTPL Leave can only be cancelled if it is Approved", title="Invalid Status Change")

		# Add any necessary validation logic here
		employee_doc = frappe.get_doc("Employee", self.employee)
		if employee_doc.external_reporting_manager == 1:
			external_report_to = employee_doc.external_report_to
			report_to = frappe.db.get_value("Employee Pull", external_report_to, "employee")
			self.is_external_manager = 1
			self.external_manager = report_to
			self.approver = ""
		else:
			if employee_doc.reports_to:
				user = frappe.db.get_value("Employee", employee_doc.reports_to, "user_id")
				if user:
					self.approver = user
					self.is_external_manager = 0
					self.external_manager = ""

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
				from_date = self.approved_from_date
				to_date = self.approved_to_date

				if not (from_date and to_date):
					frappe.throw("Approved From Date and Approved To Date are required")

				total_days = date_diff(to_date, from_date) + 1
				casual_leave = "Casual Leave"
				lwp = "Leave Without Pay"

				cl_balance = get_leave_balance_on(
					employee=self.employee,
					leave_type=casual_leave,
					date=from_date,
					consider_all_leaves_in_the_allocation_period=True
				) or 0

				cl_days = min(cl_balance, total_days)
				lwp_days = total_days - cl_days

				if cl_days > 0:
					cl_to_date = add_days(from_date, cl_days - 1)
					self.make_leave_application(
						leave_type=casual_leave,
						from_date=from_date,
						to_date=cl_to_date,
						total_days=cl_days
					)

				if lwp_days > 0:
					lwp_from_date = add_days(from_date, cl_days)
					self.make_leave_application(
						leave_type=lwp,
						from_date=lwp_from_date,
						to_date=to_date,
						total_days=lwp_days
					)

	def make_leave_application(self, leave_type, from_date, to_date, total_days):
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