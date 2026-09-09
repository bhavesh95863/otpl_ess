// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Payroll', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Get Employees'), () => fetch_employees(frm));
			frm.add_custom_button(__('Calculate Salary'), () => calculate_salary(frm))
				.addClass('btn-primary');
		}
		frm.add_custom_button(__('View Calculation'), () => view_calculation(frm));

		if (!frm.is_new()) {
			frm.add_custom_button(__('Download Salary Sheet'), () => {
				open_url_post(frappe.request.url, {
					cmd: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.download_salary_sheet',
					payroll: frm.doc.name,
				});
			});
		}

		if (frm.doc.docstatus === 1 && !frm.doc.salary_entries_created) {
			// Normally done automatically on submit; this is the retry path
			// after fixing whatever configuration made it fail.
			frm.add_custom_button(__('Retry Salary Entry'), () => create_salary_entries(frm))
				.addClass('btn-primary');
		}
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Process TDS Entry'), () => process_tds_entries(frm));
		}
		if (frm.doc.docstatus === 1 && frm.doc.salary_entries_created) {
			frm.add_custom_button(__('TDS Entry'), () => {
				// TDS vouchers belong to the OTPL TDS register, not to this
				// payroll, so they are found by the month they were posted in.
				frappe.set_route('List', 'Journal Entry', {
					otpl_ref_doctype: 'OTPL TDS',
					posting_date: frm.doc.to_date,
				});
			}, __('View'));
			frm.add_custom_button(__('Journal Entry'), () => {
				frappe.set_route('List', 'Journal Entry', {
					otpl_ref_doctype: 'OTPL Payroll',
					otpl_ref_name: frm.doc.name,
				});
			}, __('View'));
			frm.add_custom_button(__('Salary Payable Request'), () => {
				frappe.set_route('List', 'Salary Payable Request', { otpl_payroll: frm.doc.name });
			}, __('View'));
		}

		if (frm.doc.from_date && frm.doc.to_date) {
			const d = frappe.datetime;
			const days = d.get_diff(frm.doc.to_date, frm.doc.from_date) + 1;
			frm.set_value('days_in_period', days);
		}
	},

	from_date: refresh_days,
	to_date: refresh_days,

	get_employees: fetch_employees,
	calculate_payroll: calculate_salary,
});

function refresh_days(frm) {
	if (frm.doc.from_date && frm.doc.to_date) {
		const days = frappe.datetime.get_diff(frm.doc.to_date, frm.doc.from_date) + 1;
		frm.set_value('days_in_period', days);
	}
}

function fetch_employees(frm) {
	if (!frm.doc.from_date || !frm.doc.to_date) {
		frappe.msgprint(__('Please set From Date and To Date first.'));
		return;
	}
	frappe.call({
		method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.get_employees',
		args: { doc: frm.doc },
		freeze: true,
		freeze_message: __('Fetching Employees...'),
		callback(r) {
			if (!r.message || !r.message.length) {
				frappe.msgprint(__('No employees match the filters.'));
				return;
			}
			frm.clear_table('employees');
			r.message.forEach((emp) => {
				const child = frm.add_child('employees');
				Object.assign(child, emp);
			});
			frm.refresh_field('employees');
			frappe.show_alert({
				message: __('{0} employees fetched. Click Calculate Salary.', [r.message.length]),
				indicator: 'green',
			});
		},
	});
}

function calculate_salary(frm) {
	if (!frm.doc.from_date || !frm.doc.to_date) {
		frappe.msgprint(__('Please set From Date and To Date first.'));
		return;
	}
	frappe.call({
		method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.calculate_payroll',
		args: { doc: frm.doc },
		freeze: true,
		freeze_message: __('Calculating Salary...'),
		callback(r) {
			if (!r.message) return;
			const { rows = [], log = [], allocations = [] } = r.message;
			frm.clear_table('employees');
			rows.forEach((row) => {
				const child = frm.add_child('employees');
				Object.assign(child, row);
			});
			// Previewed here; validate() recomputes them authoritatively on save.
			frm.clear_table('order_allocations');
			allocations.forEach((alloc) => {
				const child = frm.add_child('order_allocations');
				Object.assign(child, alloc);
			});
			frm.refresh_field('order_allocations');
			frm.refresh_field('employees');
			if (log && log.length) {
				frm.set_value('processing_log', log.join('\n'));
			}
			// validate() refreshes nets and totals when the user saves.
			frappe.show_alert({
				message: __('Calculated for {0} rows. Review and Save.', [rows.length]),
				indicator: 'green',
			});
		},
	});
}

// ---------------------------------------------------------------------------
// View Calculation dialog
// ---------------------------------------------------------------------------
function view_calculation(frm, prefill_emp) {
if (!frm.doc.from_date || !frm.doc.to_date) {
frappe.msgprint(__('Please set From Date and To Date first.'));
return;
}
const choices = (frm.doc.employees || [])
		.map((r) => r.employee)
		.filter(Boolean);
	if (!choices.length) {
		frappe.msgprint(__('No employees in the table. Run Get Employees / Calculate first.'));
		return;
	}

	const d = new frappe.ui.Dialog({
		title: __('Calculation Breakdown'),
		size: 'large',
		fields: [
			{
				fieldname: 'employee', fieldtype: 'Link', label: __('Employee'),
				options: 'Employee',
				get_query: () => ({ filters: { name: ['in', choices] } }),
				default: prefill_emp || choices[0],
},
{ fieldname: 'output', fieldtype: 'HTML' },
],
});

const render = () => {
const emp = d.get_value('employee');
if (!emp) return;
d.fields_dict.output.$wrapper.html(
`<div class="text-muted" style="padding:12px">${__('Loading...')}</div>`
);
frappe.call({
method:
'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.get_calculation_trace',
args: { doc: frm.doc, employee: emp },
callback(r) {
if (!r.message || !r.message.steps) {
d.fields_dict.output.$wrapper.html(
`<div class="text-muted">${__('No data')}</div>`
);
return;
}
const html = r.message.steps
.map((sec) => {
const rows = sec.items
.map(
([k, v]) => `
<tr>
<td style="white-space:nowrap; vertical-align:top;
           padding:4px 12px 4px 0; font-weight:500;">
${frappe.utils.escape_html(k)}
</td>
<td style="vertical-align:top; padding:4px 0;">
${frappe.utils.escape_html(String(v))}
</td>
</tr>`
)
.join('');
return `
<h5 style="margin-top:14px; margin-bottom:6px">
${frappe.utils.escape_html(sec.section)}
</h5>
<table class="table table-sm" style="font-size:12px">
<tbody>${rows}</tbody>
</table>`;
})
.join('');
d.fields_dict.output.$wrapper.html(html);
},
});
};

d.fields_dict.employee.df.onchange = render;
d.show();
render();
}

// Per-row trigger: click the small "?" indicator inside a grid row to see
// the calculation for that employee.
frappe.ui.form.on('OTPL Payroll Detail', {
employee(frm, cdt, cdn) {
// no-op; just here so the child has a registered handler
},
});

// Add a button at the top of the child grid row form (when the user opens a row)
frappe.ui.form.on('OTPL Payroll', {
onload_post_render(frm) {
const grid = frm.fields_dict.employees && frm.fields_dict.employees.grid;
if (!grid) return;
grid.wrapper.on('click', '.btn-otpl-explain', function () {
const cdn = $(this).attr('data-cdn');
const row = locals['OTPL Payroll Detail'][cdn];
if (row && row.employee) view_calculation(frm, row.employee);
});
},
});

function create_salary_entries(frm) {
	frappe.confirm(
		__('Post the salary journal entries (one per business vertical) for this payroll?'),
		() => {
			frappe.call({
				method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.create_salary_entries',
				args: { payroll: frm.doc.name },
				freeze: true,
				freeze_message: __('Creating Salary Entries...'),
				callback(r) {
					if (!r.message) return;
					const { created = [], skipped = [], fallback_used = [] } = r.message;
					const links = created
						.map((n) => `<a href="/app/journal-entry/${encodeURIComponent(n)}">${n}</a>`)
						.join('<br>');
					let msg = __('Posted {0} Journal Entries:', [created.length]) + '<br>' + links;
					if (skipped.length) {
						msg += '<br><br><b>' + __('Skipped {0}:', [skipped.length]) + '</b><br>' + skipped.join('<br>');
					}
					if (fallback_used.length) {
						msg += '<br><br><b>' + __('Booked to the default sales order ({0}) - no attendance order and no Employee master order:', [fallback_used.length])
							+ '</b><br>' + fallback_used.join('<br>');
					}
					frappe.msgprint({ title: __('Salary Entries Created'), message: msg, indicator: 'green' });
					frm.reload_doc();
				},
			});
		}
	);
}

function process_tds_entries(frm) {
	const month = frappe.datetime.str_to_obj(frm.doc.to_date).toLocaleString('en-US', { month: 'long' });
	frappe.confirm(
		__('Post the TDS journal entries for {0} for every employee in this payroll that has an OTPL TDS record?', [month]),
		() => {
			frappe.call({
				method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.create_tds_entries',
				args: { payroll: frm.doc.name },
				freeze: true,
				freeze_message: __('Processing TDS Entries...'),
				callback(r) {
					if (!r.message) return;
					const { created = [], skipped = [], month: booked_month } = r.message;
					const links = created
						.map((c) => `${c.employee_name || c.employee}: ${format_currency(c.amount)} — `
							+ `<a href="#Form/Journal Entry/${encodeURIComponent(c.journal_entry)}">${c.journal_entry}</a>`)
						.join('<br>');
					let msg = __('Posted {0} TDS entries for {1}:', [created.length, booked_month])
						+ '<br>' + links;
					if (skipped.length) {
						msg += '<br><br><b>' + __('Already posted ({0}):', [skipped.length]) + '</b><br>'
							+ skipped.join('<br>');
					}
					frappe.msgprint({ title: __('TDS Entries'), message: msg, indicator: 'green' });
				},
			});
		}
	);
}
