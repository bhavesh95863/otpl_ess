// Copyright (c) 2024, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Expense', {
	refresh: function(frm) {
		if(frm.doc.docstatus == 1){
			frm.add_custom_button(__('View Payment Entry'), function () {
				frappe.route_options = {"otpl_ref_doctype": frm.doc.doctype, "otpl_ref_name": frm.doc.name};
				frappe.set_route("List", "Payment Entry");
			}, __("View Entry"));
			frm.add_custom_button(__('View Journal Entry'), function () {
				frappe.route_options = {"otpl_ref_doctype": frm.doc.doctype, "otpl_ref_name": frm.doc.name};
				frappe.set_route("List", "Journal Entry");
			}, __("View Entry"));
		}
	},
	approve: function(frm) {
		frm.call({
			method:"approve_expense",
			doc:frm.doc,
			async:false,
			callback:function(r){
				frm.reload_doc()
			}
		})
	}
});
