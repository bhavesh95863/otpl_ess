# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_to_remote_erp

class OTPLLeave(Document):
	def on_update(self):
		"""
		Trigger sync to remote ERP when leave is saved with external manager
		"""
		# Push to remote ERP if external manager is set
		push_leave_to_remote_erp(self)
