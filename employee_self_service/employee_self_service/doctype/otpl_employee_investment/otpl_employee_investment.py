# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document


class OTPLEmployeeInvestment(Document):
	def validate(self):
		# enforce uniqueness on (employee, fiscal_year)
		exists = frappe.db.get_value(
			"OTPL Employee Investment",
			{
				"employee": self.employee,
				"fiscal_year": self.fiscal_year,
				"name": ["!=", self.name],
			},
			"name",
		)
		if exists:
			frappe.throw(
				_("Investment record already exists for {0} in {1}: {2}").format(
					self.employee, self.fiscal_year, exists
				)
			)
