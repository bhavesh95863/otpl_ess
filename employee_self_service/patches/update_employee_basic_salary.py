import frappe


def execute():
    """
    Update basic_salary for all employees based on advance_to_be_deducted.
    Sets basic_salary = advance_to_be_deducted / 2 for all employee records.
    """
    frappe.db.sql("""
        UPDATE `tabEmployee`
        SET basic_salary = CASE 
            WHEN advance_to_be_deducted > 0 THEN advance_to_be_deducted / 2
            ELSE 0
        END
    """)
    
    frappe.db.commit()
    
    print("Successfully updated basic_salary for all employees")
