# Ubutaka — MINALOC land registry

A Flask and MySQL application for the blockchain-based land registration and ownership verification case study. The Ministry of Local Government (MINALOC) is the institution in the study. Parcel records live in MariaDB, and every registration, legal update, and ownership check is sealed into a hash chain.

## Run it

1. Start MySQL from the XAMPP control panel.
2. Install the Python packages:

```
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

3. Start the application:

```
python run.py
```

4. Open http://127.0.0.1:5000

The database `minaloc_land_registry` is created on first launch. The connection uses `127.0.0.1`, user `root`, and an empty password, which matches a default XAMPP install. Change those values in `landregistry/config.py` if your MySQL account is different.

## Accounts

| Role | Username | Password |
| --- | --- | --- |
| Administrator | admin | admin |
| Authorized land official | mukamana | Officer1 |
| Landowner | uwase | Uwase123 |
| Landowner | habimana | Habimana1 |

The administrator password is the predefined value `admin`. The application restores that password on startup, and the account cannot be deactivated. Every other password must be at least 6 characters.

Only a landowner can self-register. The administrator creates authorized land official accounts. The sample landowner and official accounts above are preloaded so the case study has records to open.

## What each role can do

- Landowners open their own account, submit land for approval, download or print a record after the UPI and title are issued, and open Reports for their own parcels.
- Authorized land officials approve or reject a landowner's application. Approval generates the UPI and the title and seals the parcel. They also update a record when a legal approval requires it.
- Administrators issue official accounts, assign that role, activate or deactivate accounts, review the audit log, and prepare management reports.

The UPI and title exist only after an official approves the land. A parcel that is still waiting for approval will not appear in a title search.

Two sealed demo parcels are already on the register: `1/03/07/04/2581`, and `1/02/05/03/9090` where the sealed size is 800 m² and the register says 960 m². An official can open the second one and seal a correction.

This demo is not connected to a MINALOC system. Records stay in this application.

## Validation

- A name can contain letters and spaces only.
- A username cannot contain spaces, cannot start with a number or a special character, and otherwise uses letters, numbers, and underscores.
- Email must be a valid address. Phone must be a Rwandan mobile number such as `0781234567` or `+250781234567`.
- A new password must not be less than 6 characters.
- A landowner national ID is 16 digits.

## Reports

Signed-in users can open Reports, filter the register, and download the same selection as PDF or CSV. Landowners only receive rows for their own parcels. User and activity reports stay with the administrator.
