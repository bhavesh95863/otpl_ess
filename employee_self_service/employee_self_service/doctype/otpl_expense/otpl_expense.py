# -*- coding: utf-8 -*-
# Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document

class OTPLExpense(Document):
	def approve_expense(self):
		# Implement the logic for approving the expense here
		if not frappe.session.user == self.approval_manager and not frappe.session.user == "Administrator":
			frappe.throw("Only the assigned manager can approve this expense.")
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to approve the expense.")
		self.approved_by_manager = 1
		self.save()

	def on_submit(self):
		if not self.approved_by_manager:
			frappe.throw("Expense cannot be submitted without manager approval.")
		
		# create journal entry on submit
		self.create_jv()

	def on_cancel(self):
		# Cancel any Journal Entries / Payment Entries linked to this OTPL Expense
		filters = {"otpl_ref_doctype": self.doctype, "otpl_ref_name": self.name, "docstatus": 1}
		jv_list = frappe.get_all("Journal Entry", filters=filters, fields=["name"])
		for row in jv_list:
			jv_doc = frappe.get_doc("Journal Entry", row.name)
			jv_doc.flags.ignore_permissions = True
			jv_doc.cancel()

		pe_list = frappe.get_all("Payment Entry", filters=filters, fields=["name"])
		for row in pe_list:
			pe_doc = frappe.get_doc("Payment Entry", row.name)
			pe_doc.flags.ignore_permissions = True
			pe_doc.cancel()

	def after_delete(self):
		# remove linked Journal Entries / Payment Entries if force deleting
		filters = {"otpl_ref_doctype": self.doctype, "otpl_ref_name": self.name}
		jv_list = frappe.get_all("Journal Entry", filters=filters, fields=["name"])
		for row in jv_list:
			frappe.delete_doc("Journal Entry", row.name, ignore_permissions=True, force=1)

		pe_list = frappe.get_all("Payment Entry", filters=filters, fields=["name"])
		for row in pe_list:
			frappe.delete_doc("Payment Entry", row.name, ignore_permissions=True, force=1)

	def create_jv(self):
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to create Journal Entry.")

		# get expense account from OTPL Expense Type -> OTPL Expense Type Accounts mapping
		expense_account = None
		if self.expense_claim_type and self.business_line:
			expense_account = frappe.db.get_value(
				"OTPL Expense Type Accounts",
				{"parent": self.expense_claim_type, "business_line": self.business_line},
				"expense_account"
			)

		if not expense_account:
			frappe.throw("Expense account not configured for selected Expense Type and Business Line.")

		# payroll payable account from Business Line
		payable_account = frappe.db.get_value("Business Line", self.business_line, "payroll_payable")
		if not payable_account:
			frappe.throw("Payroll Payable (employee payable) account not configured on Business Line.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")

		# Build Journal Entry
		jv_doc = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": self.date_of_expense or self.date_of_entry,
			"voucher_type": "Journal Entry",
			"company": company,
			"user_remark": self.details_of_expense or self.purpose,
			"accounts": [
				{
					"account": expense_account,
					"debit_in_account_currency": self.amount_approved,
				},
				{
					"account": payable_account,
					"credit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.sent_by,
				}
			],
		})

		# set reference back to this OTPL Expense so reports/cancels can find the JE
		jv_doc.otpl_ref_doctype = self.doctype
		jv_doc.otpl_ref_name = self.name

		jv_doc.flags.ignore_mandatory = True
		jv_doc.flags.ignore_permissions = True
		jv_doc = jv_doc.insert()
		jv_doc.submit()

		# save reference on OTPL Expense (if field exists)
		if hasattr(self, "journal_entry"):
			frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)
		else:
			# still useful to store reference via db_set to a custom field if present
			try:
				frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)
			except Exception:
				# ignore if field missing; reference fields on JE already set
				pass
	