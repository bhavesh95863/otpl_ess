// Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('ESS Location', {
	refresh: function(frm) {
		set_field_requirements(frm);
		set_travel_staff_type_options(frm);
	},

	location_depend_team_leader: function(frm) {
		set_field_requirements(frm);
	}
});

// Pull the "Staff Type" options straight from the Employee doctype so this
// dropdown always mirrors it — a new option added on Employee shows up here with
// no change to this doctype. No separate Staff Type master is maintained.
function set_travel_staff_type_options(frm) {
	frappe.model.with_doctype('Employee', function () {
		const df = frappe.meta.get_docfield('Employee', 'staff_type');
		if (!df || !df.options) return;
		frm.fields_dict.travel_out_of_location_staff_types.grid.update_docfield_property(
			'staff_type', 'options', df.options
		);
	});
}

function set_field_requirements(frm) {
	if (frm.doc.location_depend_team_leader == 1) {
		frm.set_df_property('latitude', 'reqd', 0);
		frm.set_df_property('longitude', 'reqd', 0);
		frm.set_df_property('radius', 'reqd', 0);
	} else {
		frm.set_df_property('latitude', 'reqd', 1);
		frm.set_df_property('longitude', 'reqd', 1);
		frm.set_df_property('radius', 'reqd', 1);
	}
}
