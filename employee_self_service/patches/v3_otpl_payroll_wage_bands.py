# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe


def execute():
	"""Migrate legacy ESS Location.max_wages → max_wage_pf.

	After this round of OTPL Payroll changes:
	  * `max_wages` is renamed to `max_wage_pf` (PF upper cap)
	  * a new `max_wage_esic` field is added (ESIC upper cap)

	`bench migrate` adds the new columns from the JSON; this patch copies
	values from the legacy column into `max_wage_pf` so existing rows keep
	working.
	"""
	if not frappe.db.has_column("ESS Location", "max_wage_pf"):
		# Migrate hasn't created the new column yet — nothing to back-fill.
		return

	if frappe.db.has_column("ESS Location", "max_wages"):
		frappe.db.sql(
			"""
			UPDATE `tabESS Location`
			SET max_wage_pf = max_wages
			WHERE COALESCE(max_wage_pf, 0) = 0
			  AND COALESCE(max_wages, 0) > 0
			"""
		)
		frappe.db.commit()

	frappe.clear_cache(doctype="ESS Location")
