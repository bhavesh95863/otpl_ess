import frappe

def validate_employee(doc, method):
    """
    Validate and update basic_salary based on advance_to_be_deducted.
    Sets basic_salary = advance_to_be_deducted / 2 if advance_to_be_deducted > 0, else sets it to 0.
    """
    if doc.advance_to_be_deducted > 0:
        doc.basic_salary = doc.advance_to_be_deducted / 2
    else:
        doc.basic_salary = 0