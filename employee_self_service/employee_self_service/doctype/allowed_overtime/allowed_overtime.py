# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class AllowedOvertime(Document):
	def validate(self):
		"""Validate that the same employee doesn't have duplicate entry for same date"""
		if self.is_new():
			existing = frappe.db.exists("Allowed Overtime", {
				"employee": self.employee,
				"date": self.date,
				"name": ["!=", self.name]
			})
			if existing:
				frappe.throw("An Allowed Overtime entry already exists for {0} on {1}".format(
					self.employee, self.date
				))
