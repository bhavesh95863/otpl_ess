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
// no change to this doctype. The child doctype stores no options of its own, so
// this is the only thing that populates the dropdown; ESSLocation.validate()
// re-checks the saved rows against the same Employee field on the server.
// NOTE: this is a v12 bench — grid.update_docfield_property() does not exist
// here (v13+ only). Patch the child docfield through frappe.meta instead.
function set_travel_staff_type_options(frm) {
	frappe.model.with_doctype('Employee', function () {
		const source = frappe.meta.get_docfield('Employee', 'staff_type');
		if (!source || !source.options) return;

		const options = source.options.split('\n').map(o => o.trim()).filter(o => o);
		// leading blank so a row can be cleared before it is filled in
		const value = [''].concat(options).join('\n');

		// The v12 grid resolves this docfield two ways — the shared docfield_map
		// and the per-document copy — so both have to be set or the dropdown comes
		// up empty depending on which path rendered the row.
		const child = 'ESS Location Travel Staff Type';
		[
			frappe.meta.get_docfield(child, 'staff_type'),
			frappe.meta.get_docfield(child, 'staff_type', frm.doc.name)
		].forEach(function (df) {
			if (df) df.options = value;
		});

		frm.refresh_field('travel_out_of_location_staff_types');
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
