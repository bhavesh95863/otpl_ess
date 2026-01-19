# Worker Attendance Management System - Implementation Summary

## Overview
This implementation adds comprehensive attendance management for Workers (staff type = "Worker") with location other than "Site". The system includes holiday validation, early/late check-in restrictions, and automatic checkout functionality.

## New Features Implemented

### 1. Allowed Overtime DocType
**Location:** `apps/employee_self_service/employee_self_service/employee_self_service/doctype/allowed_overtime/`

**Purpose:** Manage employee permissions for overtime, early entry, and late exit.

**Fields:**
- **Date:** The specific date for which permissions apply
- **Employee:** Link to Employee doctype
- **Employee Name:** Auto-fetched from Employee
- **Overtime Allowed:** Yes/No - Permission to work on holidays/Sundays
- **Early Entry Allowed:** Yes/No - Permission to check-in before shift start time
- **Late Exit Allowed:** Yes/No - Permission to check-out after shift end time

**Features:**
- Unique constraint: One entry per employee per date
- Auto-naming: Format `AO-{date}-{employee}`
- Permissions: System Manager (full), HR Manager (full), HR User (read-only)

**Usage:**
Create entries in this form to grant employees special permissions for specific dates.

---

### 2. Worker Attendance Validation System
**Location:** `apps/employee_self_service/employee_self_service/employee_self_service/utils/worker_attendance.py`

**Key Functions:**

#### `validate_worker_checkin(employee, log_type, checkin_time)`
Validates check-in/check-out attempts for Workers (location != "Site").

**Validation Rules:**
1. **Holiday Check-in:** 
   - If checking in on a holiday/Sunday, system checks Allowed Overtime form
   - Only allows check-in if Overtime Allowed = "Yes"
   - Shows message: "You are on leave today" if not allowed

2. **Early Check-in:**
   - If checking in before shift start time
   - Checks if Early Entry Allowed = "Yes" in Allowed Overtime
   - If not allowed: Adjusts time to shift start and shows message
   - Message: "Early check in not allowed, checkin recorded at [shift_start_time]"

3. **Late Check-out:**
   - If checking out after shift end time
   - Checks if Late Exit Allowed = "Yes" in Allowed Overtime
   - If not allowed: Adjusts time to shift end and shows message
   - Message: "Late check out is not allowed, check out recorded at [shift_end_time]"

**Time Reference:** All shift times are based on ESS Location settings.

#### `auto_checkout_workers()`
Automatic checkout for Workers who checked in but didn't check out.

**Scheduled Job:** Runs at 9:00 PM daily (21:00)

**Logic:**
- Finds all Workers (location != "Site") who checked in today without checkout
- Creates automatic checkout at shift end time (from ESS Location)
- Default checkout time: 6:00 PM if no ESS Location configured

**Result:** After auto-checkout runs, attendance processing will mark these employees as Absent.

#### `process_worker_attendance_with_hours(employee, location, date)`
Special attendance processing for Workers.

**Rules:**
1. **Both Check-in and Check-out present:**
   - Status: Present
   - Working Hours: Calculated automatically
   - Remarks: "Worker attendance with X hours"

2. **Only Check-in present (no checkout):**
   - Status: Absent
   - Working Hours: 0
   - Remarks: "Worker - Check-in only, no check-out recorded"

3. **No Check-in:**
   - Status: Absent
   - Working Hours: 0
   - Remarks: "Worker - No check-in recorded"

---

### 3. Integration Points

#### Daily Attendance Processing
**File:** `apps/employee_self_service/employee_self_service/employee_self_service/utils/daily_attendance.py`

**Modified:** `process_employee_attendance()` function
- Now calls `process_worker_attendance_with_hours()` for Workers
- Returns early with Worker-specific result if applicable
- Falls back to standard processing for non-Workers or Site employees

#### Employee Checkin Hook
**File:** `apps/employee_self_service/employee_self_service/employee_self_service/utils/otpl_attendance.py`

**Modified:** `after_employee_checkin_insert()` function
- Validates Worker check-in/check-out using `validate_worker_checkin()`
- Adjusts time if early/late restrictions apply
- Prevents check-in on holidays without permission
- Shows appropriate messages to user

#### Scheduled Jobs
**File:** `apps/employee_self_service/employee_self_service/hooks.py`

**Added:** Auto-checkout scheduled job
```python
"0 21 * * *": [
    "employee_self_service.employee_self_service.utils.auto_checkout.auto_checkout_site_employees",
    "employee_self_service.employee_self_service.utils.worker_attendance.auto_checkout_workers"
]
```

---

## Workflow Example

### Scenario 1: Normal Check-in/Check-out
**Employee:** John (Worker, Location: Office)
**ESS Location:** Shift 9:30 AM - 6:00 PM

1. **10:00 AM:** John checks in → ✓ Recorded at 10:00 AM
2. **6:30 PM:** John checks out → ✓ Recorded at 6:30 PM
3. **Next day midnight:** Attendance marked as Present with 8.5 hours

### Scenario 2: Early Check-in (Not Allowed)
**Employee:** Jane (Worker, Location: Office)
**No Allowed Overtime entry for today**

1. **8:00 AM:** Jane checks in
2. **System:** Adjusts time to 9:30 AM (shift start)
3. **Message:** "Early check in not allowed, checkin recorded at 9:30 AM"
4. **6:00 PM:** Jane checks out normally
5. **Next day:** Present with 8.5 hours (9:30 AM to 6:00 PM)

### Scenario 3: Holiday Check-in (Allowed)
**Employee:** Mike (Worker, Location: Office)
**Date:** Sunday (holiday)
**Allowed Overtime:** Entry exists with Overtime Allowed = "Yes"

1. **10:00 AM:** Mike checks in → ✓ Allowed
2. **4:00 PM:** Mike checks out
3. **Next day:** Present with 6 hours

### Scenario 4: Holiday Check-in (Not Allowed)
**Employee:** Sarah (Worker, Location: Office)
**Date:** Sunday (holiday)
**No Allowed Overtime entry**

1. **10:00 AM:** Sarah tries to check in
2. **System:** Blocks check-in, deletes the entry
3. **Message:** "You are on leave today"

### Scenario 5: Missing Check-out (Auto Checkout)
**Employee:** Tom (Worker, Location: Office)

1. **9:30 AM:** Tom checks in
2. **End of day:** Tom forgets to check out
3. **9:00 PM:** Auto-checkout job runs, creates checkout at 6:00 PM
4. **Next day midnight:** Attendance marked as Absent (despite auto-checkout)

### Scenario 6: Late Check-out (Allowed)
**Employee:** Lisa (Worker, Location: Office)
**Allowed Overtime:** Entry exists with Late Exit Allowed = "Yes"

1. **9:30 AM:** Lisa checks in
2. **8:00 PM:** Lisa checks out → ✓ Recorded at 8:00 PM
3. **Next day:** Present with 10.5 hours

---

## Configuration Steps

### 1. Setup ESS Location
Navigate to: **ESS Location** doctype

Ensure the following fields are configured:
- Shift Start Time (e.g., 09:30:00)
- Shift End Time (e.g., 18:00:00)
- Late Arrival Threshold
- Early Exit Threshold

### 2. Employee Setup
Navigate to: **Employee** doctype

Ensure:
- Staff Type = "Worker"
- Location = (anything except "Site")
- Holiday List is assigned

### 3. Create Allowed Overtime Entries
Navigate to: **Allowed Overtime** doctype

Create entries for employees who need:
- Holiday/Sunday work permission
- Early check-in permission
- Late check-out permission

**Example Entry:**
- Date: 2026-01-19
- Employee: HR-EMP-00123
- Overtime Allowed: Yes
- Early Entry Allowed: No
- Late Exit Allowed: Yes

### 4. Verify Scheduled Jobs
Check that scheduled jobs are active:
```bash
bench --site [site-name] doctor
```

Look for:
- Daily attendance processing at midnight (0 0 * * *)
- Auto-checkout at 9 PM (0 21 * * *)

---

## Testing Checklist

### Test Case 1: Normal Attendance
- [ ] Worker checks in during shift hours
- [ ] Worker checks out during shift hours
- [ ] Attendance marked as Present with correct hours

### Test Case 2: Early Check-in (Not Allowed)
- [ ] Worker checks in before shift start
- [ ] No Allowed Overtime entry
- [ ] Time adjusted to shift start
- [ ] Message displayed to user

### Test Case 3: Late Check-out (Not Allowed)
- [ ] Worker checks out after shift end
- [ ] No Allowed Overtime entry
- [ ] Time adjusted to shift end
- [ ] Message displayed to user

### Test Case 4: Holiday Check-in (Allowed)
- [ ] Create Allowed Overtime with Overtime Allowed = Yes
- [ ] Worker checks in on holiday
- [ ] Check-in successful

### Test Case 5: Holiday Check-in (Not Allowed)
- [ ] No Allowed Overtime entry
- [ ] Worker tries to check in on holiday
- [ ] Check-in blocked with message

### Test Case 6: Missing Check-out
- [ ] Worker checks in
- [ ] Worker doesn't check out
- [ ] Wait for 9 PM scheduled job
- [ ] Auto-checkout created at shift end time
- [ ] Next day: Attendance marked as Absent

### Test Case 7: Site Workers (Excluded)
- [ ] Worker with Location = "Site"
- [ ] Check-in/out works normally
- [ ] No special validations applied

---

## Database Schema

### Allowed Overtime
```sql
CREATE TABLE `tabAllowed Overtime` (
  `name` varchar(140) PRIMARY KEY,
  `date` date,
  `employee` varchar(140),
  `employee_name` varchar(140),
  `overtime_allowed` varchar(10),
  `early_entry_allowed` varchar(10),
  `late_exit_allowed` varchar(10),
  ...
  INDEX `employee_date_index` (`employee`, `date`)
);
```

---

## API Reference

### validate_worker_checkin(employee, log_type, checkin_time)
**Returns:** `(is_valid, message, adjusted_time)`
- `is_valid`: Boolean - Whether check-in is allowed
- `message`: String - Message to display to user
- `adjusted_time`: datetime - Adjusted time (if time was modified)

### get_allowed_overtime(employee, date)
**Returns:** Dict with allowed_overtime record or None

### is_holiday_for_employee(employee, date)
**Returns:** Boolean - True if date is holiday

### auto_checkout_workers()
**Returns:** Dict with status and processed count

### process_worker_attendance_with_hours(employee, location, date)
**Returns:** String - "Processed", "Absent", "Skipped", or None

---

## Troubleshooting

### Issue: Check-in blocked on regular workday
**Solution:** Check if date is in Holiday List. If yes, create Allowed Overtime entry.

### Issue: Time not adjusting for early check-in
**Solution:** 
1. Verify ESS Location is configured
2. Check shift_start_time is set
3. Ensure no Allowed Overtime entry with Early Entry Allowed = Yes

### Issue: Auto-checkout not running
**Solution:**
1. Check scheduler is enabled: `bench --site [site] enable-scheduler`
2. Verify cron job: `bench --site [site] doctor`
3. Check logs: `tail -f logs/frappe.log`

### Issue: Attendance still Present despite missing checkout
**Solution:** Auto-checkout creates checkout record but Worker logic marks as Absent. Check:
1. Attendance was processed after 9 PM
2. Worker staff type is correctly set
3. Location is not "Site"

---

## Future Enhancements

1. **Notification System:**
   - Send SMS/Email when check-out is auto-created
   - Alert managers of absent Workers

2. **Grace Period:**
   - Configurable grace period for late/early check-in
   - Example: 15 minutes grace before adjustment

3. **Overtime Calculation:**
   - Calculate actual overtime hours
   - Link to payroll system

4. **Reporting:**
   - Worker attendance summary report
   - Overtime hours report
   - Missing checkout report

5. **Mobile App Integration:**
   - Show allowed overtime status in mobile app
   - Pre-check before allowing check-in

---

## Support

For issues or questions:
1. Check error logs: `apps/employee_self_service/logs/`
2. Review Frappe logs: `logs/frappe.log`
3. Contact: Nesscale Solutions Private Limited

---

**Version:** 1.0
**Date:** January 18, 2026
**Author:** Nesscale Solutions Private Limited
