# RescuePlate 🌿 — Full-stack food-rescue demonstration

**Bilingual Arabic/English web application** in cream + forest-green + gold, with separate student/cafeteria/admin experiences, a FastAPI backend, SQLite transactional storage, and usable QR collection codes. Delivered as a local academic **DEMO**, not an operational food retail service.

## Quick start (Windows / macOS / Linux)

Requires **Python 3.10+** and internet once to install Python packages.

```bash
cd rescueplate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. API docs **http://127.0.0.1:8000/api/docs**. No separate frontend server, npm, or database server is needed; frontend files are included under `static/`.

**Windows alternative:** double-click `START_WINDOWS.bat` to install the packages and run the server. Internet is needed for the first installation only.

## Ready-to-use demo accounts

| Type | Email | Password |
|---|---|---|
| Student | `student@rescueplate.local` | `Demo@12345` |
| Cafeteria | `cafe@rescueplate.local` | `Demo@12345` |
| Second cafeteria | `kitchen@rescueplate.local` | `Demo@12345` |
| Administrator | `admin@rescueplate.local` | `DemoAdmin@123!` |

Only for a local test: **never put these accounts/passwords on an open public server**. Demo records are initialized once in the SQLite database upon the first start. Delete only the demo DB when you want to reset the local demonstration; this erases any accounts and bookings in it.

## Suggested live demonstration for the professor

1. Login with the **Student** test account.
2. Browse demo meals; click Reserve. The selected meal decreases by one in SQLite inside an immediate write transaction.
3. Visit **My Reservations** to see the QR and pickup token.
4. Logout and login using the **Cafeteria** account that owns that meal (Campus Café, Green Kitchen, or Fresh Corner).
5. Select the **Pickup Orders** tab and paste the pickup token. The server verifies ownership, the reserved status, and time window before confirming. The same token cannot be collected twice.
6. Login as **Admin** to see the actual database statistics, approve/suspend cafeterias and inspect audit records.
7. As a student try opening `/api/admin/overview` from the API docs; API responds 403. Rejected requests appear in the audit log.
8. Create a new **Cafeteria** account using the registration screen; it remains pending until Admin approves it.

## What this version actually implements

- Responsive public page and meal finder; Arabic (RTL) / English (LTR) switch.
- Student registration/sign-in, reservations, QR display, cancellation, real database inventory updates.
- Partner registration/approval, add meals, inventory adjustment, pause/resume meals, collection with token.
- Admin stats, approval workflow, security/audit events.
- PBKDF2-SHA256 password hashing with per-user salts, opaque random HTTP-only cookie session tokens (only hashes stored server-side), server-side RBAC, SameSite cookies, origin verification for unsafe HTTP methods, basic IP based login-rate limiter, standard HTTP security headers, parameterized SQL queries, single-use unpredictable pickup tokens.
- Transactional reservation and collection; server-side ownership checks; expiry-on-request of stale reservations.

## Bilingual language support / اللغات

Select **العربية** or **English** in the header. All built-in screens, actions, category labels, statuses, admin headings and demo meal details have corresponding text in both languages. The document direction switches automatically between RTL and LTR; selection is saved in browser localStorage.

New cafes and meals can supply **Arabic and English versions** of names, addresses, and descriptions. The platform displays the appropriate version. If a partner leaves the optional Arabic description blank, the original authored description is used instead; this project does **not** use automatic machine translation. Names entered by real users are always displayed as provided.

## Documented demo meal prices / مصادر الأسعار

See `Meal_Data_Sources.csv` (Excel-friendly UTF-8 CSV) and `static/Meal_Data_Sources.html` (public, bilingual source page). Eight original menu prices were checked **2026-11-06** against the [El Haty published menu](https://elhaty19638.com/menu/menu.html). Every price in the seeded demo catalog maps to a specific menu item. All rescue discounts, partner cafeterias, stocks and availability are fabricated for academic testing and are NOT actual El Haty surplus offers, nor do they imply an affiliation. Menu prices change over time.

**Existing local databases:** Schema migration adds the localized columns automatically. If you had previously launched the old demo, it will retain old meal rows, so use a *new, empty, disposable demo database* to see the newly verified 8-item sample catalog. Back up existing SQLite files before replacing them; never delete a real operating database.

## Demo versus real-world use

This project runs as a **demo** (`DEMO_MODE=1`). For private engineering evaluation, set `DEMO_MODE=0`, use a **separate persistent DB**, provide `ADMIN_PASSWORD` and `COOKIE_SECURE=1` behind HTTPS. **Not production-ready without additional work**, including regulated food-safety checks, restaurant verification, privacy policies, email confirmation, secure backup/restore, operational monitoring, full security audit, browser testing, and scalable persistent infrastructure. It does **not** process money, send SMS, verify real business licenses, assert that surplus food is safe, nor establish partnerships with referenced restaurants. Do not claim it does.

Some important practical constraints: expiry transitions are handled during API requests, not background jobs; in-memory rate limits reset on process restart and are not distributed; SQLite should have persistent disk storage and should not be scaled across separate server instances with independent files. For larger scale use a PostgreSQL migration and central rate-limiting store. Real payment, maps, email, camera-scanner and file uploads were intentionally left outside this dependable demo scope.

## Tests

```bash
python test_workflow.py
```

Runs a disposable database and checks public browsing, authentication, role restrictions, student reservation and stock decrease, QR, single-use collection, admin approval. The test must print **PASS**.

## Fair discounts & longer booking / pickup times (2026-11 update)

- The demo retains roughly **15–20% simulated discounts**. Original menu-price references are unchanged.
- **Default maximum total window: 120 minutes.** The demo opens approximately two minutes before first seed/refresh and ends approximately 118 minutes afterward.
- **Booking closes 60 minutes after the pickup window begins**. An order already booked can be picked up until the separate final pickup deadline (up to **60 more minutes**). For shorter custom windows, booking closes at least 10 minutes before pickup ends.
- Example: open listing at **14:00**, close bookings at **15:00**, last collection **16:00**. A student who booked at 14:20 may collect any time during the allowed pickup period, not only after 15:00.
- Cafeterias can set shorter windows as needed, but the backend rejects listings lasting longer than **120 minutes** or extending more than three hours into the future. The actual safe food holding deadline always takes priority.
- **Food safety:** the two-hour window is a product workflow limit, **not a food safety certification**. Real food providers must verify cooking/holding conditions, storage temperature, allergen details and the food-specific last safe time; some foods must have shorter windows.
- Existing databases keep previous listings unmodified; to renew only the built-in demo offers and adopt the new window, stop the server and run `REFRESH_DEMO_WINDOWS.bat` (or `python refresh_demo_offers.py`). The script backs up the database and leaves offers with active reservations untouched. Never run it in live mode.
- For a fully fresh local demonstration you may create a new disposable demo DB, but do not remove a DB containing accounts or bookings you want to keep.

## November 2026 quality and security update

- **Booking window:** the booking API refuses reservations before the advertised start, closes booking after 60 minutes (or ten minutes before pickup ends, whichever is sooner), and allows pickup within the stated period up to 120 minutes. This is a business rule, **not** a food-safety certification.
- **Available inventory:** the `quantity` column represents *unreserved available servings*. A reservation atomically decrements this number, while a cancellation increments it once when the meal has not expired. Partners can set the remaining available inventory for active offers, but cannot rewrite existing confirmed reservations; inventory changes are audited. Paused or expired offers cannot have their stock edited.
- **Food-safety disclosure:** new listings must include preparation time, a declared safe-until time, ingredients, known allergens, and storage instructions. A listing whose pickup end exceeds its declared safety limit is rejected. Demo data has unverified safety information and is **not** edible stock or a food-safety endorsement.
- **CSRF hardening:** mutating endpoints require a same-origin X-Requested-With request header and, for existing sessions, a per-session X-CSRF-Token. The frontend obtains the token using `/api/csrf`. Sessions use HTTP-only cookies and are not stored in localStorage. All create/update/reserve requests now carry a JSON payload, including `{}` for requests with no parameters.
- **Meal artwork:** self-contained original SVG illustrations replace emoji-only meal thumbnails. These are clearly **illustrations**, not photographs of verified products. We did not claim to use licensed restaurant photographs.
- **Browser testing:** `python test_workflow.py` covers backend routes, authorization, booking windows, QR replay refusal, food-safety input validation, stock changes, and CSRF checks. Static JS syntax is validated with `node --check static/app.js`. A graphical browser test might be unavailable due to the restricted build environment; visually test Arabic, English and narrow-screen views on the deployment host before a public launch.
- **Production caution:** `DEMO_MODE=0` disables auto-seeding but does **not** automatically remove seeded accounts from an existing SQLite database. For a real launch use a separate new production database, strong `ADMIN_PASSWORD`, HTTPS with `COOKIE_SECURE=1`, a separate deployment configuration, privacy policies, backups, user verification, safe-food procedures and independent security review. The project is an educational prototype, not production-certified food commerce software.


## Windows one-click start (updated)

Double-click `START_WINDOWS.bat` after extracting the ZIP. It installs the Python dependencies, waits for the API health check to succeed, and **then** opens the site in the default browser. Keep the black terminal window open. If port 8000 is occupied by an older RescuePlate instance, the launcher automatically chooses an available port between 8000 and 8109 and prints the actual link. Do not open `static/index.html` directly.

On a slow first load, the homepage retries loading backend data automatically and displays a Retry button if it cannot connect. Open `http://127.0.0.1:<shown-port>/health` to verify API health; API docs are at `/api/docs`. Existing user data in `rescueplate.sqlite3` is preserved when using the same extracted project directory.

The demo prices and discount percentages remain unchanged from the latest submitted version.
