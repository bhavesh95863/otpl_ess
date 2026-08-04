# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document


@frappe.whitelist()
def get_staff_type_options():
	"""The live "Staff Type" options, read straight off the Employee doctype (where
	staff_type is a Custom Field). Nothing is duplicated in this app, so an option
	added or renamed on Employee takes effect here with no code change."""
	df = frappe.get_meta("Employee").get_field("staff_type")
	if not (df and df.options):
		return []
	return [opt.strip() for opt in df.options.split("\n") if opt.strip()]


class ESSLocation(Document):
	def validate(self):
		self.validate_travel_staff_types()

	def validate_travel_staff_types(self):
		"""The child staff_type field stores no options of its own, so Frappe's Select
		validation never fires for it — check the rows against Employee here instead."""
		options = get_staff_type_options()
		if not options:
			return

		seen = set()
		for row in self.travel_out_of_location_staff_types:
			if row.staff_type not in options:
				frappe.throw(
					_("Row #{0}: {1} is not a valid Staff Type. Valid options are: {2}").format(
						row.idx, frappe.bold(row.staff_type), ", ".join(options)
					)
				)

			if row.staff_type in seen:
				frappe.throw(
					_("Row #{0}: Staff Type {1} is listed more than once.").format(
						row.idx, frappe.bold(row.staff_type)
					)
				)
			seen.add(row.staff_type)
