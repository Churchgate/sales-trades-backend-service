# Export Launchpad Boot Camp 2026 — wtcabuja.com integration

This is the handover for the two endpoints wtcabuja.com's Export Launchpad
apply flow needs to call: registration capture, and eligibility-document
upload. Both are **public and unauthenticated** — no API key, no login.

## Base URL

```
https://api-production-6348.up.railway.app
```

CORS is already open for `https://wtcabuja.com` and `https://www.wtcabuja.com`
— no proxy needed, call directly from the browser.

## Current status: test mode

The program's Freshsales CRM sync is **off** right now
(`crm_sync_enabled: false` in the program config). Registrations and
documents submitted today are saved and fully visible in the internal
dashboard, but are **not** pushed to the live CRM. We'll flip this on once
the integration is verified end-to-end — no action needed on your side when
that happens, existing "pending" registrations will sync automatically.

---

## 1. Register a company

```
POST /api/v1/trade/programs/export-launchpad-2026/register
Content-Type: application/json
```

### Request body

```json
{
  "first_name": "Amaka",
  "last_name": "Eze",
  "email": "amaka@example.com",
  "phone": "+2348012345678",
  "company": "De Pafek Foods and Spices Merchants Ltd",
  "job_title": "Founder",
  "responses": {
    "registered_address": "12 Example Street",
    "city": "Abuja",
    "postal_code": "900001",
    "country": "Nigeria",
    "company_founded": "2-5 years",
    "industry_sector": "Food & Beverage",
    "sector_specification": "Spices and condiments",
    "sector_other": null,
    "ownership": ["Woman-owned"],
    "operating_currency": "NGN",
    "fiscal_year_start": "January",
    "employee_count": "1-10",
    "sources_internationally": "No",
    "source_countries": [],
    "sells_internationally": "No",
    "sales_countries": [],
    "topics_of_interest": ["Export Bootcamp", "Tariff Playbook"],
    "consent_terms": true,
    "consent_data_processing": true,
    "consent_liability_waiver": true,
    "consent_photo_video": true,
    "cohort_date": "2026-08-20",
    "wtc_location": "Abuja",
    "second_participant": {
      "first_name": "Goodness",
      "last_name": "Alabi",
      "email": "goodness@example.com",
      "phone": "+2348099999999",
      "job_title": "Co-founder"
    }
  }
}
```

Field notes:

- `first_name`, `last_name`, `email` are required. Everything else is optional.
- `email` must be a valid email address (used for dedup — see below).
- `responses` is a free-form object — send whatever the form collects under
  the keys shown above (all optional, unrecognized keys are simply ignored,
  and every key is preserved verbatim for our records even if not listed
  above).
- `second_participant` is **optional** — omit it entirely, or send an empty
  object, if the company only registers one person. Only `first_name` and
  `last_name` are needed to create the second participant; if both are
  blank/missing, no second participant is created even if the object is
  present.
- If the second participant's email happens to match the primary's, we drop
  their email (keep the rest) rather than rejecting the submission — they
  won't be a separately CRM-syncable contact in that case.

### Response — `201 Created`

```json
{
  "status_code": 201,
  "created": true,
  "registration": {
    "registration_id": "1e507cb7-115d-48d6-bca7-436dc92dc29c",
    "participants": [
      {
        "id": 42,
        "registration_id": "1e507cb7-115d-48d6-bca7-436dc92dc29c",
        "participant_index": 1,
        "is_primary": true,
        "first_name": "Amaka",
        "last_name": "Eze",
        "email": "amaka@example.com",
        "eligibility_status": "not_requested",
        "crm_sync_status": "pending",
        "...": "(full participant record — see /trade/participants/{id} in the internal API for the complete field list)"
      },
      { "...": "second participant, if present" }
    ]
  }
}
```

**Save `registration_id` from this response** — it's required for the
eligibility document upload step below (and for the applicant to reference if
they contact support). It is *not* the same as `id` (the numeric participant
row id).

Resubmitting with the **same email** doesn't create a duplicate — it updates
the existing registration in place and returns `"created": false` with the
same `registration_id`. Safe to retry on a network error.

### Error responses

| Status | When |
|---|---|
| `404` | `slug` in the URL doesn't match a known program (should only happen if the URL is mistyped) |
| `409` | The program exists but isn't currently accepting registrations (e.g. registration hasn't opened yet, or the cohort is closed) |
| `422` | Request body validation failed (missing/invalid `first_name`, `last_name`, or `email`) |

---

## 2. Upload an eligibility document

Once a company has a `registration_id`, they can upload eligibility
documents — one file per request.

```
POST /api/v1/trade/programs/export-launchpad-2026/eligibility
Content-Type: multipart/form-data
```

### Form fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `registration_id` | text | yes | From the `/register` response above |
| `document_key` | text | yes | One of the values below |
| `file` | file | yes | Max **15 MB** |

### Valid `document_key` values

| Key | Label | Required for the cohort? |
|---|---|---|
| `cac_certificate` | CAC Certificate | **Yes** |
| `logo` | Company Logo | **Yes** |
| `company_profile` | Company Profile / Brochure | No |
| `business_plan` | Business Plan | No |

Uploading a `document_key` that isn't in this list returns `400`. Uploading
the **same** `document_key` again for the same `registration_id` **replaces**
the previous file — this is how a company corrects a mistaken upload, no
special "delete" step needed.

### Example (JavaScript `fetch`)

```js
const form = new FormData();
form.append("registration_id", registrationId);
form.append("document_key", "cac_certificate");
form.append("file", fileInput.files[0]);

const res = await fetch(
  "https://api-production-6348.up.railway.app/api/v1/trade/programs/export-launchpad-2026/eligibility",
  { method: "POST", body: form },
);
```

### Response — `201 Created`

```json
{
  "status_code": 201,
  "eligibility_status": "pending",
  "document": {
    "id": 7,
    "document_key": "cac_certificate",
    "file_name": "cac.pdf",
    "content_type": "application/pdf",
    "size_bytes": 245011,
    "uploaded_at": "2026-07-24T15:10:00Z",
    "download_url": null
  }
}
```

`eligibility_status` reflects the registration as a whole after this upload:

- `not_requested` — no documents uploaded yet
- `pending` — some, but not yet all, of the **required** documents (CAC
  certificate + logo) are in
- `submitted` — both required documents are in (optional ones don't block
  this)

This is a good field to poll/display back to the applicant as a progress
indicator ("2 of 2 required documents received").

### Error responses

| Status | When |
|---|---|
| `404` | Unknown `slug`, or `registration_id` doesn't match any registration (double-check it was copied exactly from the `/register` response) |
| `400` | `document_key` isn't one of the four values above |
| `413` | File exceeds 15 MB |
| `503` | Our storage isn't configured yet on our end — not something you can fix; contact us if you see this |

---

## Questions / issues

Contact the dashboard team (Eyimofe) if anything here doesn't match what
you're seeing live, or if you need the required-documents list changed.
