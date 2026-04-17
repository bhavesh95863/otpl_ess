# -*- coding: utf-8 -*-
# Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import flt, today
from employee_self_service.employee_self_service.utils.erp_sync import push_expense_to_remote_erp


class OTPLExpense(Document):
	def validate(self):
		if not self.approval_manager or not self.external_manager:
			employee_doc = frappe.get_doc("Employee", self.sent_by)
			if employee_doc.external_reporting_manager == 1:
				external_report_to = employee_doc.external_report_to
				report_to = frappe.db.get_value("Employee Pull", external_report_to, "employee")
				self.is_external_manager = 1
				self.external_manager = report_to
				self.approval_manager = ""
			else:
				if employee_doc.reports_to:
					user = frappe.db.get_value("Employee", employee_doc.reports_to, "user_id")
					if user:
						self.is_external_manager = 0
						self.external_manager = ""
						self.approval_manager = user
		if not self.business_line:
			self.business_line = frappe.db.get_value("Employee", self.sent_by, "business_vertical")

		# Calculate item totals for GST expense categories
		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			self.calculate_item_totals()

		# Validate amount_approved cannot exceed original amount (only for non-GST categories)
		if self.expense_category not in ("With GST Invoice", "Without GST Invoice"):
			if self.amount_approved and self.amount:
				if self.amount_approved > self.amount:
					frappe.throw("Amount Approved cannot be greater than the original Amount")

		# Validate transfer_to_employee is set for Other Employee Transfer
		if self.expense_category == "Other Employee Transfer" and not self.transfer_to_employee:
			frappe.throw("Please select the employee to whom the fund is being transferred.")

	def calculate_item_totals(self):
		"""Calculate totals from expense_items table and compute tax from taxes_and_charges template"""
		total_amount = 0
		for row in self.get("expense_items", []):
			base = flt(row.rate) * flt(row.quantity)
			row.amount = base
			total_amount += base

		self.total_amount = total_amount

		# Calculate tax from the selected Purchase Taxes and Charges Template
		total_tax = 0
		if self.taxes_and_charges:
			tax_rows = frappe.get_all(
				"Purchase Taxes and Charges",
				filters={"parent": self.taxes_and_charges, "parenttype": "Purchase Taxes and Charges Template"},
				fields=["charge_type", "rate"],
				order_by="idx asc"
			)
			for tax in tax_rows:
				if tax.charge_type == "On Net Total":
					total_tax += flt(total_amount) * flt(tax.rate) / 100
				elif tax.charge_type == "On Previous Row Total":
					total_tax += flt(total_amount + total_tax) * flt(tax.rate) / 100

		self.total_gst_amount = total_tax
		self.total_with_gst = total_amount + total_tax
		# Auto-set approved amount from grand total
		self.amount_approved = self.total_with_gst

	def on_update(self):
		"""Trigger sync to remote ERP when expense is saved with external manager"""
		push_expense_to_remote_erp(self)

	def approve_expense(self):
		if not frappe.session.user == self.approval_manager and not frappe.session.user == "Administrator":
			frappe.throw("Only the assigned manager can approve this expense.")

		# --- Validate all required fields before approval ---
		missing = []

		if not self.sent_by:
			missing.append("Sent By")
		if not self.date_of_entry:
			missing.append("Date of Entry")
		if not self.date_of_expense:
			missing.append("Date of Expense")
		if not self.expense_claim_type:
			missing.append("Expense Claim Type")
		if not self.expense_category:
			missing.append("Expense Category")
		if not self.business_line:
			missing.append("Business Line")
		if not self.amount or flt(self.amount) <= 0:
			missing.append("Amount")
		if not self.details_of_expense:
			missing.append("Details of Expense")

		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			if not self.gst_number:
				missing.append("GST Number")
			if not self.supplier:
				missing.append("Supplier")
			if not self.get("expense_items") or len(self.get("expense_items")) == 0:
				missing.append("Expense Items (at least one item row)")
			else:
				for idx, row in enumerate(self.get("expense_items"), 1):
					if not row.item:
						missing.append(f"Item in Row {idx}")
					if not row.quantity or flt(row.quantity) <= 0:
						missing.append(f"Quantity in Row {idx}")
					if not row.rate or flt(row.rate) <= 0:
						missing.append(f"Rate in Row {idx}")
			if not self.total_amount or flt(self.total_amount) <= 0:
				missing.append("Total Amount must be greater than zero")

		elif self.expense_category == "Other Employee Transfer":
			if not self.transfer_to_employee:
				missing.append("Transfer To Employee")
			if not self.amount_approved or flt(self.amount_approved) <= 0:
				missing.append("Amount Approved")

		else:
			# With Cash Memo or no category (backward compat)
			if not self.amount_approved or flt(self.amount_approved) <= 0:
				missing.append("Amount Approved")

		if missing:
			frappe.throw(
				"The following fields are required before approval:<br><br>• " + "<br>• ".join(missing),
				title="Missing Required Fields"
			)

		self.approved_by_manager = 1
		self.save()

	def on_submit(self):
		if not self.approved_by_manager:
			frappe.throw("Expense cannot be submitted without manager approval.")

		if self.expense_category in ("With GST Invoice", "Without GST Invoice"):
			self.create_material_request_and_po()
		elif self.expense_category == "With Cash Memo":
			self.create_expense_jv()
		elif self.expense_category == "Other Employee Transfer":
			self.create_transfer_jv()
		else:
			# Default: existing JV flow for backward compatibility
			self.create_expense_jv()

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

		# Cancel Material Request if linked
		if self.material_request:
			mr_doc = frappe.get_doc("Material Request", self.material_request)
			if mr_doc.docstatus == 1:
				mr_doc.flags.ignore_permissions = True
				mr_doc.cancel()

		# Cancel Purchase Order if linked (only if still in draft or submitted)
		if self.purchase_order:
			po_doc = frappe.get_doc("Purchase Order", self.purchase_order)
			if po_doc.docstatus == 1:
				po_doc.flags.ignore_permissions = True
				po_doc.cancel()

	def after_delete(self):
		# remove linked Journal Entries / Payment Entries if force deleting
		filters = {"otpl_ref_doctype": self.doctype, "otpl_ref_name": self.name}
		jv_list = frappe.get_all("Journal Entry", filters=filters, fields=["name"])
		for row in jv_list:
			frappe.delete_doc("Journal Entry", row.name, ignore_permissions=True, force=1)

		pe_list = frappe.get_all("Payment Entry", filters=filters, fields=["name"])
		for row in pe_list:
			frappe.delete_doc("Payment Entry", row.name, ignore_permissions=True, force=1)

		# Delete linked Material Request
		if self.material_request:
			frappe.delete_doc("Material Request", self.material_request, ignore_permissions=True, force=1)
		# Delete linked Purchase Order
		if self.purchase_order:
			frappe.delete_doc("Purchase Order", self.purchase_order, ignore_permissions=True, force=1)

	# ─── GST Invoice flow: Material Request + Purchase Order ───

	def create_material_request_and_po(self):
		"""Create Material Request (auto-submitted) and Purchase Order (draft) for GST expense categories"""
		if not self.expense_items or len(self.expense_items) == 0:
			frappe.throw("Please add items in the Expense Items table before submitting.")
		if not self.supplier:
			frappe.throw("Please select a Supplier before submitting.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
		default_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if not default_warehouse:
			# Fallback: pick first warehouse for the company
			default_warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
		if not default_warehouse:
			frappe.throw("No default warehouse configured. Please set one in Stock Settings.")

		expense_date = self.date_of_expense or self.date_of_entry or today()

		# --- Create Material Request (Material Indent) ---
		mr_items = []
		for row in self.expense_items:
			mr_items.append({
				"item_code": row.item,
				"item_name": row.item_name,
				"qty": row.quantity,
				"schedule_date": expense_date,
				"warehouse": default_warehouse,
				"uom": frappe.db.get_value("Item", row.item, "stock_uom") or "Nos",
				"stock_uom": frappe.db.get_value("Item", row.item, "stock_uom") or "Nos",
			})

		mr_doc = frappe.get_doc({
			"doctype": "Material Request",
			"material_request_type": "Purchase",
			"company": company,
			"transaction_date": expense_date,
			"schedule_date": expense_date,
			"items": mr_items,
		})
		mr_doc.flags.ignore_permissions = True
		mr_doc.flags.ignore_mandatory = True
		mr_doc.insert()
		mr_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "material_request", mr_doc.name)

		# --- Create Purchase Order (draft - for manual approval) ---
		po_items = []
		for idx, row in enumerate(self.expense_items):
			po_items.append({
				"item_code": row.item,
				"item_name": row.item_name,
				"qty": row.quantity,
				"rate": row.rate,
				"schedule_date": expense_date,
				"warehouse": default_warehouse,
				"uom": frappe.db.get_value("Item", row.item, "stock_uom") or "Nos",
				"stock_uom": frappe.db.get_value("Item", row.item, "stock_uom") or "Nos",
				"material_request": mr_doc.name,
				"material_request_item": mr_doc.items[idx].name if idx < len(mr_doc.items) else None,
			})

		po_data = {
			"doctype": "Purchase Order",
			"supplier": self.supplier,
			"company": company,
			"transaction_date": expense_date,
			"schedule_date": expense_date,
			"items": po_items,
		}
		if self.taxes_and_charges:
			po_data["taxes_and_charges"] = self.taxes_and_charges

		po_doc = frappe.get_doc(po_data)
		po_doc.flags.ignore_permissions = True
		po_doc.flags.ignore_mandatory = True
		po_doc.insert()
		# PO is NOT submitted - it will be approved/submitted manually

		frappe.db.set_value(self.doctype, self.name, "purchase_order", po_doc.name)

		frappe.msgprint(
			f"Material Request <b>{mr_doc.name}</b> submitted and Purchase Order <b>{po_doc.name}</b> created (pending approval).",
			title="Entries Created",
			indicator="green"
		)

	# ─── Cash Memo flow: Expense Journal Entry ───

	def create_expense_jv(self):
		"""Create Journal Entry for expense (Cash Memo or default flow)"""
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to create Journal Entry.")

		expense_account = None
		if self.expense_claim_type and self.business_line:
			expense_account = frappe.db.get_value(
				"OTPL Expense Type Accounts",
				{"parent": self.expense_claim_type, "business_line": self.business_line},
				"expense_account"
			)

		if not expense_account:
			frappe.throw("Expense account not configured for selected Expense Type and Business Line.")

		payable_account = frappe.db.get_value("Business Line", self.business_line, "payroll_payable")
		if not payable_account:
			frappe.throw("Payroll Payable (employee payable) account not configured on Business Line.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
		order_cost_center = frappe.db.get_value("Cost Center", {"sales_order": self.sales_order}, "name")

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
					"cost_center": order_cost_center
				},
				{
					"account": payable_account,
					"credit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.sent_by,
					"cost_center": order_cost_center
				}
			],
		})

		jv_doc.otpl_ref_doctype = self.doctype
		jv_doc.otpl_ref_name = self.name
		jv_doc.flags.ignore_mandatory = True
		jv_doc.flags.ignore_permissions = True
		jv_doc = jv_doc.insert()
		jv_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)

	# ─── Other Employee Transfer flow: Transfer JV ───

	def create_transfer_jv(self):
		"""Create Journal Entry to transfer funds to another employee"""
		if not self.transfer_to_employee:
			frappe.throw("Please select the employee to transfer funds to.")
		if not self.amount_approved or self.amount_approved <= 0:
			frappe.throw("Amount Approved must be greater than zero to create transfer Journal Entry.")

		# Fetch payroll payable for the sender (giving funds) via their business vertical
		sender_business_vertical = frappe.db.get_value("Employee", self.sent_by, "business_vertical")
		if not sender_business_vertical:
			frappe.throw(f"Business Vertical not set for employee {self.sent_by}.")
		sender_payable = frappe.db.get_value("Business Line", sender_business_vertical, "payroll_payable")
		if not sender_payable:
			frappe.throw(f"Payroll Payable account not configured on Business Line {sender_business_vertical}.")

		# Fetch payroll payable for the receiver (receiving funds) via their business vertical
		receiver_business_vertical = frappe.db.get_value("Employee", self.transfer_to_employee, "business_vertical")
		if not receiver_business_vertical:
			frappe.throw(f"Business Vertical not set for employee {self.transfer_to_employee}.")
		receiver_payable = frappe.db.get_value("Business Line", receiver_business_vertical, "payroll_payable")
		if not receiver_payable:
			frappe.throw(f"Payroll Payable account not configured on Business Line {receiver_business_vertical}.")

		company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
		order_cost_center = frappe.db.get_value("Cost Center", {"sales_order": self.sales_order}, "name")

		transfer_employee_name = frappe.db.get_value("Employee", self.transfer_to_employee, "employee_name")

		jv_doc = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": self.date_of_expense or self.date_of_entry,
			"voucher_type": "Journal Entry",
			"company": company,
			"user_remark": f"Transfer from {self.employee_name or self.sent_by} to {transfer_employee_name or self.transfer_to_employee} - {self.details_of_expense or self.purpose}",
			"accounts": [
				{
					"account": receiver_payable,
					"debit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.transfer_to_employee,
					"cost_center": order_cost_center
				},
				{
					"account": sender_payable,
					"credit_in_account_currency": self.amount_approved,
					"party_type": "Employee",
					"party": self.sent_by,
					"cost_center": order_cost_center
				}
			],
		})

		jv_doc.otpl_ref_doctype = self.doctype
		jv_doc.otpl_ref_name = self.name
		jv_doc.flags.ignore_mandatory = True
		jv_doc.flags.ignore_permissions = True
		jv_doc = jv_doc.insert()
		jv_doc.submit()

		frappe.db.set_value(self.doctype, self.name, "journal_entry", jv_doc.name)
		frappe.msgprint(
			f"Transfer Journal Entry <b>{jv_doc.name}</b> created and submitted.",
			title="Transfer Entry Created",
			indicator="green"
		)


@frappe.whitelist()
def get_tax_details(template_name):
	"""Fetch tax rows from a Purchase Taxes and Charges Template"""
	if not template_name:
		return []
	return frappe.get_all(
		"Purchase Taxes and Charges",
		filters={"parent": template_name, "parenttype": "Purchase Taxes and Charges Template"},
		fields=["charge_type", "rate"],
		order_by="idx asc"
	)


@frappe.whitelist()
def get_supplier_by_gstin(gstin):
	"""Look up a Supplier by GST number from linked Address records"""
	if not gstin:
		return None

	result = frappe.db.sql("""
		SELECT dl.link_name as supplier
		FROM `tabAddress` a
		INNER JOIN `tabDynamic Link` dl ON dl.parent = a.name AND dl.parenttype = 'Address'
		WHERE a.gstin = %s AND dl.link_doctype = 'Supplier'
		LIMIT 1
	""", gstin, as_dict=1)

	if result:
		return result[0].supplier
	return None


def on_purchase_order_submit(doc, method):
	"""Hook: when a Purchase Order linked to an OTPL Expense is submitted,
	auto-create Purchase Receipt and Purchase Invoice."""
	expense_name = frappe.db.get_value(
		"OTPL Expense",
		{"purchase_order": doc.name, "docstatus": 1},
		"name"
	)
	if not expense_name:
		return

	company = doc.company
	# --- Create Purchase Receipt (auto-submitted) ---
	pr_items = []
	for item in doc.items:
		pr_items.append({
			"item_code": item.item_code,
			"item_name": item.item_name,
			"qty": item.qty,
			"rate": item.rate,
			"warehouse": item.warehouse,
			"uom": item.uom,
			"stock_uom": item.stock_uom,
			"purchase_order": doc.name,
			"purchase_order_item": item.name,
		})

	pr_doc = frappe.get_doc({
		"doctype": "Purchase Receipt",
		"supplier": doc.supplier,
		"company": company,
		"posting_date": today(),
		"items": pr_items,
	})
	pr_doc.flags.ignore_permissions = True
	pr_doc.flags.ignore_mandatory = True
	pr_doc.insert()
	pr_doc.submit()

	# --- Create Purchase Invoice (auto-submitted) ---
	pi_items = []
	for item in doc.items:
		pi_items.append({
			"item_code": item.item_code,
			"item_name": item.item_name,
			"qty": item.qty,
			"rate": item.rate,
			"warehouse": item.warehouse,
			"uom": item.uom,
			"stock_uom": item.stock_uom,
			"purchase_order": doc.name,
			"po_detail": item.name,
			"purchase_receipt": pr_doc.name,
		})

	pi_doc = frappe.get_doc({
		"doctype": "Purchase Invoice",
		"supplier": doc.supplier,
		"company": company,
		"posting_date": today(),
		"items": pi_items,
	})
	pi_doc.flags.ignore_permissions = True
	pi_doc.flags.ignore_mandatory = True
	pi_doc.insert()
	pi_doc.submit()

	frappe.msgprint(
		f"Purchase Receipt <b>{pr_doc.name}</b> and Purchase Invoice <b>{pi_doc.name}</b> auto-created from OTPL Expense <b>{expense_name}</b>.",
		title="Auto Entries Created",
		indicator="green"
	)
	