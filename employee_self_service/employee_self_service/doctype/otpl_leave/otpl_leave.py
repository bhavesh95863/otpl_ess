# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_to_remote_erp

class OTPLLeave(Document):
	def validate(self):
		"""
		Validate OTPL Leave before saving
		"""
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
