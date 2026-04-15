// Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Expense', {
	refresh: function(frm) {
		if (frm.doc.docstatus == 1) {
			frm.add_custom_button(__('View Payment Entry'), function () {
				frappe.route_options = {"otpl_ref_doctype": frm.doc.doctype, "otpl_ref_name": frm.doc.name};
				frappe.set_route("List", "Payment Entry");
			}, __("View Entry"));
			frm.add_custom_button(__('View Journal Entry'), function () {
				frappe.route_options = {"otpl_ref_doctype": frm.doc.doctype, "otpl_ref_name": frm.doc.name};
				frappe.set_route("List", "Journal Entry");
			}, __("View Entry"));
		}

		// Set child table field permissions based on roles
		set_item_field_permissions(frm);
	},

	approve: function(frm) {
		frm.call({
			method: "approve_expense",
			doc: frm.doc,
			async: false,
			callback: function(r) {
				frm.reload_doc();
			}
		});
	},

	expense_category: function(frm) {
		// Clear fields when category changes
		if (frm.doc.expense_category !== 'With GST Invoice' && frm.doc.expense_category !== 'Without GST Invoice') {
			frm.set_value('gst_number', '');
			frm.set_value('supplier', '');
			frm.set_value('supplier_name', '');
			frm.doc.expense_items = [];
			frm.refresh_field('expense_items');
			frm.set_value('total_amount', 0);
			frm.set_value('total_gst_amount', 0);
			frm.set_value('total_with_gst', 0);
		}
		if (frm.doc.expense_category !== 'Other Employee Transfer') {
			frm.set_value('transfer_to_employee', '');
		}
		frm.refresh_fields();
	},

	gst_number: function(frm) {
		if (!frm.doc.gst_number) {
			frm.set_value('supplier', '');
			frm.set_value('supplier_name', '');
			return;
		}

		frappe.call({
			method: 'employee_self_service.employee_self_service.doctype.otpl_expense.otpl_expense.get_supplier_by_gstin',
			args: { gstin: frm.doc.gst_number },
			callback: function(r) {
				if (r.message) {
					frm.set_value('supplier', r.message);
					frappe.show_alert({
						message: __('Supplier found: {0}', [r.message]),
						indicator: 'green'
					});
				} else {
					frm.set_value('supplier', '');
					frm.set_value('supplier_name', '');
					frappe.confirm(
						__('No supplier found with GST Number {0}. Would you like to create a new Supplier?', [frm.doc.gst_number]),
						function() {
							frappe.new_doc('Supplier', {
								supplier_name: '',
								tax_id: frm.doc.gst_number
							});
						}
					);
				}
			}
		});
	}
});

frappe.ui.form.on('OTPL Expense Item', {
	rate: function(frm, cdt, cdn) {
		calculate_row_and_totals(frm, cdt, cdn);
	},
	quantity: function(frm, cdt, cdn) {
		calculate_row_and_totals(frm, cdt, cdn);
	},
	gst_rate: function(frm, cdt, cdn) {
		calculate_row_and_totals(frm, cdt, cdn);
	},
	expense_items_remove: function(frm) {
		calculate_totals(frm);
	}
});

function calculate_row_and_totals(frm, cdt, cdn) {
	var row = locals[cdt][cdn];
	row.amount = flt(row.rate) * flt(row.quantity);
	frm.refresh_field('expense_items');
	calculate_totals(frm);
}

function calculate_totals(frm) {
	var total_amount = 0;
	var total_gst = 0;

	(frm.doc.expense_items || []).forEach(function(row) {
		var base = flt(row.rate) * flt(row.quantity);
		row.amount = base;
		total_amount += base;
		total_gst += base * flt(row.gst_rate) / 100;
	});

	frm.set_value('total_amount', total_amount);
	frm.set_value('total_gst_amount', total_gst);
	frm.set_value('total_with_gst', total_amount + total_gst);
	frm.set_value('amount_approved', total_amount + total_gst);
	frm.refresh_field('expense_items');
}

function set_item_field_permissions(frm) {
	if (!frm.fields_dict.expense_items) return;

	var is_stock_role = frappe.user.has_role('Stock Manager') ||
						frappe.user.has_role('Stock User') ||
						frappe.user.has_role('System Manager');

	var is_accounts_role = frappe.user.has_role('Accounts Manager') ||
						   frappe.user.has_role('Accounts User') ||
						   frappe.user.has_role('System Manager');

	// Item and Quantity: editable only for Stock Manager, Stock User, System Manager
	frm.fields_dict.expense_items.grid.toggle_enable('item', is_stock_role);
	frm.fields_dict.expense_items.grid.toggle_enable('quantity', is_stock_role);

	// Rate, GST Rate, GST Type: editable only for Accounts Manager, Accounts User, System Manager
	frm.fields_dict.expense_items.grid.toggle_enable('rate', is_accounts_role);
	frm.fields_dict.expense_items.grid.toggle_enable('gst_rate', is_accounts_role);
	frm.fields_dict.expense_items.grid.toggle_enable('gst_type', is_accounts_role);
}
