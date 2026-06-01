# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""
	Add custom fields required by the 2026-05 round of OTPL Payroll feedback:
	  - Business Line.al_eligible       (Check) - enables AL calc for site workers
	  - Employee.daily_tada             (Currency) - per-present-day TADA
	  - Employee.hra_amount             (Currency) - flat HRA
	  - Employee.conveyance_amount      (Currency) - flat conveyance
	  - Employee.telephone_amount       (Currency) - flat telephone allowance
	"""

	custom_fields = {
		"Business Line": [
			{
				"fieldname": "al_eligible",
				"label": "AL Eligible (OTPL Payroll)",
				"fieldtype": "Check",
				"insert_after": "business_line",
				"description": "If checked, site workers on this business line "
				"will accrue Annual Leave in the OTPL Payroll sheet.",
			}
		],
		"Employee": [
			{
				"fieldname": "daily_tada",
				"label": "Daily TADA",
				"fieldtype": "Currency",
				"insert_after": "basic_salary",
				"description": "Per-present-day TADA. Only used for Worker/Site "
				"and Field/Site in OTPL Payroll.",
			},
			{
				"fieldname": "hra_amount",
				"label": "HRA",
				"fieldtype": "Currency",
				"insert_after": "daily_tada",
				"description": "Flat HRA. Used in OTPL Payroll Extra Allowance "
				"for everyone except Worker/Site and Field/Site.",
			},
			{
				"fieldname": "conveyance_amount",
				"label": "Conveyance",
				"fieldtype": "Currency",
				"insert_after": "hra_amount",
			},
			{
				"fieldname": "telephone_amount",
				"label": "Telephone Allowance",
				"fieldtype": "Currency",
				"insert_after": "conveyance_amount",
			},
		],
	}

	create_custom_fields(custom_fields, update=True)

	frappe.clear_cache(doctype="Employee")
	frappe.clear_cache(doctype="Business Line")
	frappe.db.commit()
