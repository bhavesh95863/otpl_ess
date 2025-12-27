# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class ERPSyncSettings(Document):
	def validate(self):
		"""Validate ERP URL format"""
		if self.erp_url:
			# Remove trailing slash
			self.erp_url = self.erp_url.rstrip('/')
			
			# Ensure URL starts with http:// or https://
			if not self.erp_url.startswith('http://') and not self.erp_url.startswith('https://'):
				frappe.throw('ERP URL must start with http:// or https://')
