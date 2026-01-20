# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe import _
from employee_self_service.employee_self_service.utils.erp_sync import push_expense_status_to_source

class ExpensePull(Document):
	def validate(self):
		"""
		Validate amount_approved cannot exceed original amount
		"""
		if self.amount_approved and self.amount:
			if self.amount_approved > self.amount:
				frappe.throw(_("Amount Approved cannot be greater than the original Amount"))
	
	def before_save(self):
		"""
		Set approval_manager_user from approval_manager employee ID
		"""
		if self.approval_manager and not self.approval_manager_user:
			# Get user_id from Employee
			user_id = frappe.db.get_value("Employee", self.approval_manager, "user_id")
			if user_id:
				self.approval_manager_user = user_id
	
	def on_update(self):
		"""
		Sync status updates back to source ERP when approved/rejected
		"""
		# Check if this record came from an external source
		if not self.source_erp:
			return
		
		# Check if status or approval changed
		if self.has_value_changed('status') or self.has_value_changed('approved_by_manager'):
			push_expense_status_to_source(self)


@frappe.whitelist()
def approve_expense(docname, amount_approved):
	"""
	Approve an expense pull request with approved amount
	This will update the approval status and trigger reverse sync to source ERP
	"""
	try:
		doc = frappe.get_doc("Expense Pull", docname)
		
		# Validate amount
		if not amount_approved:
			frappe.throw(_("Amount Approved is required"))
		
		# Update the document
		doc.approved_by_manager = 1
		doc.amount_approved = amount_approved
		doc.status = "Approved"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		frappe.msgprint(_("Expense approved successfully and synced to source ERP"))
		return {"success": True, "message": "Expense approved successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error approving expense pull"
		)
		frappe.throw(_("Error approving expense: {0}").format(str(e)))
