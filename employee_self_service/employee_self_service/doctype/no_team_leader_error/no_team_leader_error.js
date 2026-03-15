// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('No Team Leader Error', {
	refresh: function(frm) {
		frm.add_custom_button(__('Check Available Team Leaders'), function() {
			frappe.call({
				method: 'employee_self_service.employee_self_service.doctype.no_team_leader_error.no_team_leader_error.get_all_leaders_with_distance',
				args: { docname: frm.doc.name },
				freeze: true,
				freeze_message: __('Fetching team leader data...'),
				callback: function(r) {
					if (r.message && r.message.length > 0) {
						let all_leaders = r.message;

						function build_rows(leaders) {
							if (!leaders.length) {
								return '<tr><td colspan="6" style="text-align:center;color:#888;">No records match the filter.</td></tr>';
							}
							return leaders.map(function(leader) {
								let distance_display = leader.distance !== null
									? leader.distance + ' m'
									: '<em>N/A</em>';

								let status_badge = leader.within_range
									? '<span style="color:green;font-weight:bold;">&#10003; Within 100m</span>'
									: '<span style="color:red;font-weight:bold;">&#10007; Outside 100m</span>';

								let note = leader.note
									? '<br><small style="color:orange;">' + leader.note + '</small>'
									: '';

								return '<tr>' +
									'<td>' + frappe.utils.escape_html(leader.employee_name) + '</td>' +
									'<td>' + frappe.utils.escape_html(leader.employee) + '</td>' +
									'<td><span class="badge badge-' + (leader.type === 'Internal' ? 'info' : 'warning') + '">' + leader.type + '</span></td>' +
									'<td>' + frappe.utils.escape_html(leader.checkin_time) + '</td>' +
									'<td>' + distance_display + note + '</td>' +
									'<td>' + status_badge + '</td>' +
								'</tr>';
							}).join('');
						}

						let html = '<div style="margin-bottom:12px;">' +
							'<b>Employee:</b> ' + frappe.utils.escape_html(frm.doc.employee) + '&nbsp;&nbsp;' +
							'<b>Location:</b> ' + frappe.utils.escape_html(frm.doc.latitude) + ', ' + frappe.utils.escape_html(frm.doc.longitude) + '&nbsp;&nbsp;' +
							'<b>Error Time:</b> ' + frappe.utils.escape_html(frm.doc.datetime) +
							'</div>' +
							'<div style="display:flex;gap:12px;margin-bottom:10px;align-items:center;">' +
								'<div style="flex:1;">' +
									'<label style="font-size:12px;font-weight:600;margin-bottom:4px;display:block;">Filter by Employee Name</label>' +
									'<input id="filter_name" type="text" placeholder="Type to filter..." class="form-control form-control-sm" style="font-size:12px;"/>' +
								'</div>' +
								'<div style="flex:1;">' +
									'<label style="font-size:12px;font-weight:600;margin-bottom:4px;display:block;">Filter by Employee ID</label>' +
									'<input id="filter_id" type="text" placeholder="Type to filter..." class="form-control form-control-sm" style="font-size:12px;"/>' +
								'</div>' +
								'<div style="padding-top:20px;">' +
									'<button id="clear_filters" class="btn btn-xs btn-default">Clear</button>' +
								'</div>' +
							'</div>' +
							'<p style="color:#888;font-size:12px;">Showing the latest checkin/location record per leader for today up to the error time. Leaders must be within 100m to be considered.</p>' +
							'<div style="overflow-x:auto;">' +
							'<table class="table table-bordered table-sm" style="font-size:12px;min-width:700px;">' +
								'<thead style="background:#f5f5f5;">' +
									'<tr>' +
										'<th>Leader Name</th>' +
										'<th>Employee ID</th>' +
										'<th>Type</th>' +
										'<th>Last Checkin / Location Time</th>' +
										'<th>Distance from Employee</th>' +
										'<th>Status</th>' +
									'</tr>' +
								'</thead>' +
								'<tbody id="leaders_tbody">' + build_rows(all_leaders) + '</tbody>' +
							'</table>' +
							'</div>';

						let d = new frappe.ui.Dialog({
							title: __('Team Leaders Analysis — Why No Leader Was Matched'),
							size: 'extra-large',
						});
						d.$body.html('<div style="padding:16px;max-height:75vh;overflow-y:auto;">' + html + '</div>');
						d.show();
						// Force full-screen width and tall height after render
						d.$wrapper.find('.modal-dialog').css({
							'width': '96vw',
							'max-width': '96vw',
							'margin': '2vh auto',
						});
						d.$wrapper.find('.modal-content').css({
							'min-height': '80vh',
						});

						// Live filter logic
						function apply_filters() {
							let name_q = (d.$body.find('#filter_name').val() || '').toLowerCase().trim();
							let id_q   = (d.$body.find('#filter_id').val() || '').toLowerCase().trim();
							let filtered = all_leaders.filter(function(l) {
								let name_match = !name_q || l.employee_name.toLowerCase().includes(name_q);
								let id_match   = !id_q   || l.employee.toLowerCase().includes(id_q);
								return name_match && id_match;
							});
							d.$body.find('#leaders_tbody').html(build_rows(filtered));
						}

						d.$body.find('#filter_name').on('input', apply_filters);
						d.$body.find('#filter_id').on('input', apply_filters);
						d.$body.find('#clear_filters').on('click', function() {
							d.$body.find('#filter_name').val('');
							d.$body.find('#filter_id').val('');
							apply_filters();
						});
					} else {
						frappe.msgprint({
							title: __('No Team Leader Records Found'),
							indicator: 'orange',
							message: __('No internal or external team leaders had any checkin or location records for today up to the error time (<b>{0}</b>). This is why no leader was matched.', [frm.doc.datetime]),
						});
					}
				}
			});
		});
	}
});
