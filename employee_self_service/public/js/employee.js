frappe.ui.form.on('Employee', {
    refresh(frm) {  
        if(frm.doc.is_team_leader == 0 && frm.doc.show_sales_order == 0) {
            frm.set_df_property("external_sales_order", 'hidden', 1);
            frm.set_df_property("external_sales_order", 'read_only', 1);
            frm.set_df_property("business_vertical", 'read_only', 1);
            frm.set_df_property("sales_order", 'read_only', 1);
        }
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
    }
})