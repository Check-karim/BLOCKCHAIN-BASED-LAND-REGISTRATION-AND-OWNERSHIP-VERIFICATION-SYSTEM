PROVINCE_CODES = {
    "Kigali City": 1,
    "Southern": 2,
    "Western": 3,
    "Northern": 4,
    "Eastern": 5,
}

PROVINCES = {
    "Kigali City": ["Gasabo", "Kicukiro", "Nyarugenge"],
    "Eastern": ["Bugesera", "Gatsibo", "Kayonza", "Kirehe", "Ngoma", "Nyagatare", "Rwamagana"],
    "Northern": ["Burera", "Gakenke", "Gicumbi", "Musanze", "Rulindo"],
    "Southern": ["Gisagara", "Huye", "Kamonyi", "Muhanga", "Nyamagabe", "Nyanza", "Nyaruguru", "Ruhango"],
    "Western": ["Karongi", "Ngororero", "Nyabihu", "Nyamasheke", "Rubavu", "Rusizi", "Rutsiro"],
}

LAND_USES = [
    "Residential",
    "Agricultural",
    "Commercial",
    "Industrial",
    "Public",
    "Mixed",
]

LEGAL_BASES = [
    "Court order",
    "Notarial deed",
    "Succession certificate",
    "Sale agreement",
    "Administrative correction",
]

ROLE_LABELS = {
    "admin": "System administrator",
    "official": "Authorized land official",
    "landowner": "Landowner",
}

DEMO_MATCH_UPI = "1/03/07/04/2581"
DEMO_MISMATCH_UPI = "1/02/05/03/9090"

OFFICIAL_DUTIES = [
    "Register a new land parcel",
    "Enter the owner and parcel details",
    "Upload supporting documents",
    "Validate the submitted information",
    "Update a land record when a legal approval requires it",
    "Verify land ownership",
    "Submit the transaction to the ledger",
    "View transaction and record history",
]

ADMIN_DUTIES = [
    "Create and manage authorized land official accounts",
    "Assign the official role",
    "Activate or deactivate accounts",
    "Monitor system activity",
    "Review audit logs",
    "Generate management reports",
    "Record policies for this demo",
]

LANDOWNER_DUTIES = [
    "Log in and log out",
    "Add land for official approval",
    "View personal land records",
    "View the issued UPI and title",
    "View land registration history",
    "Download or print a land record",
]

STATUS_LABELS = {
    "registered": "Registered",
    "verified": "Verified",
    "pending_update": "Pending update",
    "pending_approval": "Awaiting approval",
    "rejected": "Rejected",
    "disputed": "Disputed",
    "active": "Active",
    "inactive": "Inactive",
}

ACTION_LABELS = {
    "genesis": "Genesis",
    "land_registered": "Land registered",
    "land_updated": "Land updated",
    "ownership_verified": "Ownership verified",
    "land_corrected": "Record corrected",
    "verified": "Verified",
    "mismatch": "Hash mismatch",
    "not_found": "Not found",
    "resolved": "Resolved",
    "REQUEST_VERIFICATION": "Verification requested",
    "CORRECT_LAND": "Record corrected",
    "RECORD_POLICY": "Policy recorded",
}
