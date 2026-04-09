frappe.ui.form.on('Travel Request', {
	refresh: function(frm) {
		// Calculate days on refresh
		if (frm.doc.date_of_departure && frm.doc.date_of_arrival) {
			var days = frappe.datetime.get_diff(frm.doc.date_of_arrival, frm.doc.date_of_departure) + 1;
			frm.set_value('number_of_days', days);
		}

		// Make form read-only after Approved or Rejected
		if (frm.doc.status === 'Approved' || frm.doc.status === 'Rejected') {
			frm.fields.forEach(function(field) {
				if (field.df.fieldname) {
					frm.set_df_property(field.df.fieldname, 'read_only', 1);
				}
			});
			frm.disable_save();
		}
	},
	date_of_departure: function(frm) {
		calculate_days(frm);
	},
	date_of_arrival: function(frm) {
		calculate_days(frm);
	}
});

function calculate_days(frm) {
	if (frm.doc.date_of_departure && frm.doc.date_of_arrival) {
		var days = frappe.datetime.get_diff(frm.doc.date_of_arrival, frm.doc.date_of_departure) + 1;
		if (days < 1) {
			frappe.msgprint(__('Date of Arrival cannot be before Date of Departure'));
			frm.set_value('number_of_days', 0);
		} else {
			frm.set_value('number_of_days', days);
		}
	}
}
