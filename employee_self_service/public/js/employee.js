frappe.ui.form.on('Employee', {
    refresh(frm) {
        if (frappe.session.user === "admin@oberoithermit.com" || frappe.user.has_role("System Manager")) {
            frm.set_df_property("phone_not_working", "hidden", 0);
            frm.set_df_property("from_hours", "hidden", 0);
            frm.set_df_property("to_hours", "hidden", 0);
        } else {
            frm.set_df_property("phone_not_working", "hidden", 1);
            frm.set_df_property("from_hours", "hidden", 1);
            frm.set_df_property("to_hours", "hidden", 1);
        }
        if(frm.doc.is_team_leader == 0 && frm.doc.show_sales_order == 0) {
            frm.set_df_property("external_sales_order", 'hidden', 1);
            frm.set_df_property("external_sales_order", 'read_only', 1);
            frm.set_df_property("business_vertical", 'read_only', 1);
            frm.set_df_property("sales_order", 'read_only', 1);
        }
        toggle_employee_availability(frm);

        if (!frm.is_new()) {
            frm.add_custom_button(__('ESS Information'), function () {
                show_ess_information(frm);
            }, __('ESS'));

            if (!frm.doc.travelling && (frappe.user.has_role("System Manager") || frappe.user.has_role("HR Manager"))) {
                frm.add_custom_button(__('Mark Travelling'), function () {
                    show_mark_travelling_dialog(frm);
                });
            }
        }
        if(frm.doc.sales_order) {
            frm.set_df_property("business_vertical", 'read_only', 1);
        } else {
            frm.set_df_property("business_vertical", 'read_only', 0);
        }
    },
    location(frm) {
        toggle_employee_availability(frm);
    },

    staff_type(frm) {
        toggle_employee_availability(frm);
    },

    is_team_leader(frm) {
        toggle_employee_availability(frm);
    },
    company(frm) {
        toggle_employee_availability(frm);
    },
    business_vertical(frm) {
        toggle_employee_availability(frm);
    },
    external_order(frm) {
        if(!frm.doc.external_order) {
            frm.set_value("external_so", "");
            frm.set_value("external_business_vertical", "");
        }
    },
    sales_order(frm) {
        if(frm.doc.sales_order) {
            frm.set_df_property("business_vertical", 'read_only', 1);
        } else {
            frm.set_df_property("business_vertical", 'read_only', 0);
        }
    },
    advance_to_be_deducted(frm) {
        if (frm.doc.advance_to_be_deducted > 0) {
            frm.set_value("basic_salary",frm.doc.advance_to_be_deducted / 2)
        } else {
            frm.set_value("basic_salary",0)
        }
    }
})

function toggle_employee_availability(frm) {

    let show_field = frm.doc.location === "Site"
    if (frm.doc.employee_availability == "On Leave") {
        show_field = true;
    }

    frm.set_df_property('employee_availability', 'hidden', !show_field);
}


function show_mark_travelling_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __('Create Travel Request'),
        fields: [
            {
                fieldname: 'date_of_departure',
                fieldtype: 'Date',
                label: __('Date of Departure'),
                reqd: 1,
                default: frappe.datetime.get_today()
            },
            {
                fieldname: 'date_of_arrival',
                fieldtype: 'Date',
                label: __('Date of Arrival'),
                reqd: 1,
                default: frappe.datetime.get_today()
            },
            {
                fieldname: 'col_break_1',
                fieldtype: 'Column Break'
            },
            {
                fieldname: 'purpose',
                fieldtype: 'Select',
                label: __('Purpose'),
                options: '\nGoing on Leave\nGoing back to work\nGoing for official work',
                reqd: 1
            },
            {
                fieldname: 'ticket',
                fieldtype: 'Attach',
                label: __('Ticket')
            },
            {
                fieldname: 'section_break_1',
                fieldtype: 'Section Break'
            },
            {
                fieldname: 'remarks',
                fieldtype: 'Small Text',
                label: __('Remarks')
            }
        ],
        primary_action_label: __('Mark Travelling'),
        primary_action(values) {
            frappe.call({
                method: 'employee_self_service.employee_self_service.utils.employee.mark_travelling',
                args: {
                    employee: frm.doc.name,
                    date_of_departure: values.date_of_departure,
                    date_of_arrival: values.date_of_arrival,
                    purpose: values.purpose,
                    ticket: values.ticket || '',
                    remarks: values.remarks || ''
                },
                freeze: true,
                freeze_message: __('Creating Travel Request...'),
                callback: function (r) {
                    if (r.exc) return;
                    d.hide();
                    frappe.show_alert({
                        message: __('Travel Request {0} created and approved.', [(r.message && r.message.name) || '']),
                        indicator: 'green'
                    });
                    frm.reload_doc();
                }
            });
        }
    });
    d.show();
}

function show_ess_information(frm) {
    frappe.call({
        method: 'employee_self_service.employee_self_service.utils.employee.get_ess_information',
        args: { employee: frm.doc.name },
        freeze: true,
        freeze_message: __('Fetching ESS Information...'),
        callback: function (r) {
            if (r.exc) return;
            const data = r.message || {};
            const self_row          = data.self;
            const manager           = data.reports_to;
            const external_manager  = data.external_reports_to;
            const reportees         = data.reportees || [];
            const external_reportees = data.external_reportees || [];

            /* ---- helpers ---- */
            const checkin_badge = (time) => time
                ? `<span style="color:#2e7d32;font-weight:600;">${time}</span>`
                : `<span style="color:#9e9e9e;">—</span>`;

            const device_badge = (status) => {
                if (status === 'N/A') return `<span style="color:#9e9e9e;">N/A</span>`;
                return status === 'Yes'
                    ? `<span class="badge" style="background:#1565c0;color:#fff;padding:2px 8px;border-radius:10px;">Yes</span>`
                    : `<span class="badge" style="background:#c62828;color:#fff;padding:2px 8px;border-radius:10px;">No</span>`;
            };

            const external_tag = `<span class="badge" style="background:#e65100;color:#fff;font-size:9px;padding:1px 5px;border-radius:8px;margin-left:4px;">External</span>`;

            const row_html = (row, label_tag) => {
                if (!row) return '';
                const ext = row.is_external ? external_tag : '';
                return `
                    <tr>
                        <td style="padding:8px 10px;white-space:nowrap;">
                            ${label_tag ? `<span class="badge" style="background:#546e7a;color:#fff;font-size:10px;padding:2px 6px;border-radius:8px;margin-right:4px;">${label_tag}</span>` : ''}
                            <strong>${frappe.utils.escape_html(row.employee_name)}</strong>${ext}
                            <br><small style="color:#757575;">${frappe.utils.escape_html(row.employee)} · ${frappe.utils.escape_html(row.designation || '')}</small>
                        </td>
                        <td style="padding:8px 10px;text-align:center;">${checkin_badge(row.checkin_time)}</td>
                        <td style="padding:8px 10px;text-align:center;">${device_badge(row.device_registered)}</td>
                    </tr>`;
            };

            /* ---- build table ---- */
            let body = `
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead>
                        <tr style="background:#eceff1;">
                            <th style="padding:8px 10px;text-align:left;font-weight:600;">Employee</th>
                            <th style="padding:8px 10px;text-align:center;font-weight:600;">Today's Check-In</th>
                            <th style="padding:8px 10px;text-align:center;font-weight:600;">Device Registered</th>
                        </tr>
                    </thead>
                    <tbody>`;

            /* Self */
            body += row_html(self_row, 'Self');

            /* Internal Manager */
            if (manager) {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#f9fbe7;font-size:11px;font-weight:600;color:#558b2f;letter-spacing:.5px;">REPORTS TO</td></tr>`;
                body += row_html(manager, '');
            }

            /* External Manager */
            if (external_manager) {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#fff3e0;font-size:11px;font-weight:600;color:#e65100;letter-spacing:.5px;">EXTERNAL REPORTS TO</td></tr>`;
                body += row_html(external_manager, '');
            }

            /* No manager at all */
            if (!manager && !external_manager) {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#f9fbe7;font-size:11px;font-weight:600;color:#558b2f;letter-spacing:.5px;">REPORTS TO</td></tr>`;
                body += `<tr><td colspan="3" style="padding:10px;text-align:center;color:#9e9e9e;font-style:italic;">No reporting manager assigned</td></tr>`;
            }

            /* Internal Reportees */
            if (reportees.length) {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#e8f5e9;font-size:11px;font-weight:600;color:#2e7d32;letter-spacing:.5px;">REPORTEES (${reportees.length})</td></tr>`;
                reportees.forEach(rep => { body += row_html(rep, ''); });
            } else {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#e8f5e9;font-size:11px;font-weight:600;color:#2e7d32;letter-spacing:.5px;">REPORTEES</td></tr>`;
                body += `<tr><td colspan="3" style="padding:10px;text-align:center;color:#9e9e9e;font-style:italic;">No reportees found</td></tr>`;
            }

            /* External Reportees */
            if (external_reportees.length) {
                body += `<tr><td colspan="3" style="padding:4px 10px;background:#fff3e0;font-size:11px;font-weight:600;color:#e65100;letter-spacing:.5px;">EXTERNAL REPORTEES (${external_reportees.length})</td></tr>`;
                external_reportees.forEach(rep => { body += row_html(rep, ''); });
            }

            body += `</tbody></table>`;

            /* ---- dialog ---- */
            const d = new frappe.ui.Dialog({
                title: __('ESS Information — {0}', [frm.doc.employee_name || frm.doc.name]),
                fields: [
                    {
                        fieldtype: 'HTML',
                        fieldname: 'ess_html',
                        options: body
                    }
                ]
            });
            d.show();
        }
    });
}