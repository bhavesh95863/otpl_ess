# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""
OTPL TDS
========

Month-wise TDS register: one document per Employee per Fiscal Year, holding the
amount to deduct in each month.

Each row posts on the last day of its month; the journal entry itself is made
from OTPL Payroll, which stamps the voucher back onto the month's row. A row
that carries a voucher is frozen here.
"""

from __future__ import unicode_literals

from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, get_last_day

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


class OTPLTDS(Document):
	def validate(self):
		self.validate_months()
		self.set_posting_dates()
		self.validate_booked_rows()
		self.total_tds = flt(sum(flt(row.amount) for row in self.tds_details), 2)

	def validate_months(self):
		seen = {}
		for row in self.tds_details:
			if row.month not in MONTHS:
				frappe.throw(_("Row {0}: select a Month.").format(row.idx))
			if row.month in seen:
				frappe.throw(_("Row {0}: {1} is already entered in row {2}.").format(
					row.idx, row.month, seen[row.month]))
			seen[row.month] = row.idx

	def set_posting_dates(self):
		"""The entry posts on the last day of the row's month.

		The fiscal year runs April to March, so the month alone does not fix the
		calendar year - the one that lands inside the fiscal year wins.
		"""
		start, end = frappe.db.get_value(
			"Fiscal Year", self.fiscal_year, ["year_start_date", "year_end_date"])
		start, end = getdate(start), getdate(end)

		for row in self.tds_details:
			if row.journal_entry:
				# Already posted: the voucher's date is the record.
				continue
			month_no = MONTHS.index(row.month) + 1
			posting_date = None
			for year in sorted({start.year, end.year}):
				last_day = getdate(get_last_day(date(year, month_no, 1)))
				if start <= last_day <= end:
					posting_date = last_day
					break
			if not posting_date:
				frappe.throw(_("Row {0}: {1} does not fall inside Fiscal Year {2}.").format(
					row.idx, row.month, self.fiscal_year))
			row.posting_date = posting_date

	def validate_booked_rows(self):
		"""Once the payroll has posted a row's journal entry, its month and
		amount are frozen - the voucher is the record now."""
		booked = frappe.db.sql(
			"""SELECT d.name, d.month, d.amount, d.journal_entry
			   FROM `tabOTPL TDS Detail` d
			   INNER JOIN `tabJournal Entry` je
			       ON je.name = d.journal_entry AND je.docstatus = 1
			   WHERE d.parent = %s""",
			self.name, as_dict=True,
		)
		rows = {row.name: row for row in self.tds_details}
		for old in booked:
			row = rows.get(old.name)
			if not row:
				frappe.throw(_("TDS for {0} is booked in {1} and cannot be removed.").format(
					old.month, old.journal_entry))
			if row.month != old.month or flt(row.amount, 2) != flt(old.amount, 2):
				frappe.throw(_("TDS for {0} is already booked in {1} - the amount cannot "
				               "be changed.").format(old.month, old.journal_entry))
