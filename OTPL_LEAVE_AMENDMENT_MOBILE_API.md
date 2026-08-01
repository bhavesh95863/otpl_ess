# OTPL Leave Amendment — Mobile Integration Guide

Contract for the **mobile app** to implement *Leave Amendment* — shortening an
already-approved leave when the employee returns early. The backend (doctype,
approver routing, and the cancel-old/create-new cascade) is built in the
`employee_self_service` app; the app calls the endpoints below.

---

## 1. Concept

An employee approved for, say, 10 days who returns after 7 cannot mark attendance
for the remaining days because the leave is already approved. **Leave Amendment**
fixes this:

- The employee opens the approved leave and chooses **Amend**, entering a shorter
  **From / To** range (within the original approved range, fewer days).
- The amendment goes to the **manager** for approval.
- **On approval** the backend **cancels the original leave** (which cancels its
  Leave Applications and frees the returned days for attendance) and creates a
  **new approved leave** for the amended range (with fresh Leave Applications and
  the usual CL/LWP monthly cap).
- **Only full-day leaves** may be amended — **Short Leave and Half Day are not
  amendable**.

---

## 2. Response envelope

`{ "http_status_code": 200|500, "message": "...", "data": <payload> }` — 200 success,
500 error (show `message`). Same ESS mobile auth as other `mobile.v1.*` calls.

Method path prefix: `employee_self_service.mobile.v1.leave_amendment.`

---

## 3. Endpoints

### 3.1 `get_amendable_leaves` — GET
The employee's **Approved, full-day** leaves that can be amended (Short Leave / Half
Day excluded). Use this to show the "Amend" option / picker.

**Query params (optional):** `start` (0), `page_length` (20).

**Response `data`:** array of
```json
{ "name": "LEAVE00742", "from_date": "2026-11-02", "to_date": "2026-11-11",
  "number_of_days": 10, "reason": "..." }
```
> If the app already has the open leave, it may skip this call and offer **Amend**
> whenever the leave is `status = Approved`, `short_leave = 0`, `half_day = 0`.

### 3.2 `apply_leave_amendment` — POST
Raise an amendment for the logged-in employee.

**Request body:**
```json
{ "original_leave": "LEAVE00742", "amended_from_date": "2026-11-02",
  "amended_to_date": "2026-11-08", "reason": "Returned early" }
```
- The amended range must sit **within** the original approved range and be **fewer
  days** (server enforced).
- Approver is resolved **server-side**.

**Response `data`:** `{ "name": "LAMEND00001", "status": "Pending" }`

**Errors (500):** original not Approved / is Short Leave or Half Day; amended range
outside the original or not shorter; no approver found — show `message`.

### 3.3 `get_my_leave_amendments` — GET
The employee's own amendments (newest first). Optional `status`, `start`, `page_length`.

**Response `data`:** array of
```json
{ "name": "LAMEND00001", "original_leave": "LEAVE00742",
  "original_from_date": "2026-11-02", "original_to_date": "2026-11-11",
  "amended_from_date": "2026-11-02", "amended_to_date": "2026-11-08",
  "number_of_days": 7, "reason": "...", "status": "Approved",
  "amended_leave": "LEAVE00743", "approver_name": "...", "approver_mobile_no": "..." }
```
- `amended_leave` is the NEW leave created on approval (empty until approved).

### 3.4 `get_leave_amendment_approvals` — GET  *(manager)*
Pending amendments awaiting the logged-in manager. Optional `start`, `page_length`.

**Response `data`:** array of
```json
{ "name": "LAMEND00001", "employee": "EMP/0013", "employee_name": "...",
  "applicant_mobile_no": "...", "original_leave": "LEAVE00742",
  "original_from_date": "2026-11-02", "original_to_date": "2026-11-11",
  "amended_from_date": "2026-11-02", "amended_to_date": "2026-11-08",
  "number_of_days": 7, "reason": "...", "status": "Pending" }
```

### 3.5 `get_leave_amendment_approved_list` — GET  *(manager)*
Amendments the manager has Approved — the "Approved" tab. Optional `start`, `page_length`.
Same fields as 3.4 plus `amended_leave`, `remarks`.

### 3.6 `set_leave_amendment_status` — POST  *(manager)*
Manager approves / rejects. **Approving runs the cancel-old / create-new cascade
server-side.**

**Request body:** `{ "name": "LAMEND00001", "status": "Approved", "remarks": "" }`
- `status` = `Approved` or `Rejected`. Only the resolved approver may act.

**Response `data`:**
```json
{ "name": "LAMEND00001", "status": "Approved", "amended_leave": "LEAVE00743" }
```
- On **Approved**, `amended_leave` is the newly created shorter leave. The original
  leave is now Cancelled and the returned days are free for attendance.
- On **Rejected**, nothing changes (the original leave stays as-is).

---

## 4. Pending-approval badge count

`get_pending_approval_counts` (GET) now includes a **`leave_amendment`** key, added
to `total` — alongside `leave`, `expense`, `travel`, `travelling_cl`:

```json
{ "leave": 2, "expense": 1, "travel": 0, "travelling_cl": 1,
  "leave_amendment": 1, "total": 5, "...": "..." }
```

---

## 5. Suggested app flows

**Employee**
1. Open an approved leave. If `short_leave==0 && half_day==0`, show **Amend**.
2. Pick the new (shorter) From / To within the original range → `apply_leave_amendment`.
3. Track under `get_my_leave_amendments`; once Approved, `amended_leave` is the new leave.

**Manager**
1. **Pending tab:** `get_leave_amendment_approvals`; **Approved tab:**
   `get_leave_amendment_approved_list`.
2. Approve / reject via `set_leave_amendment_status`. Approval performs the leave
   swap automatically — no other action needed.

---

## 6. Field / status reference
- **OTPL Leave Amendment** `status`: `Pending` → `Approved` / `Rejected`.
- Dates `YYYY-MM-DD`; `number_of_days` = inclusive count of the amended range.
- Not amendable: Short Leave, Half Day, or any non-Approved leave.
- **External approvers:** when the employee reports to an EXTERNAL manager, the
  amendment syncs to that manager's ERP (via `OTPL Leave Amendment Pull`) and the
  decision syncs back automatically — no app work; the app only ever calls the
  endpoints above for **internal**-approver employees.

---

*Backend reference: `doctype/otpl_leave_amendment`, `mobile/v1/leave_amendment.py`.*
