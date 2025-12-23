# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe

def execute():
	"""Setup custom fields for attendance processing"""
	
	# No custom fields needed - using standard ERPNext fields:
	# - late_entry (standard field in Attendance)
	# - early_exit (standard field in Attendance)
	# - location (standard field in Employee)
	
	frappe.db.commit()
	print("Setup complete - using standard fields")
