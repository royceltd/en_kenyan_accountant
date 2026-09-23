# WHT and VAT Withholding (WVAT)

## The two taxes, briefly

**WHT (Withholding Tax)** — Income Tax Act. Applies to specific payment types (professional
fees, consultancy/agency, contractual work, rent, dividends, interest, ...), each at its own
rate. Any business paying for a qualifying service is generally required to withhold — it is
not restricted to specially appointed agents. Reduces the payee's income tax liability for
the year.

**VAT Withholding (WVAT)** — VAT Act. 2% of the taxable (VAT-exclusive) value of a supply.
Only businesses **specifically gazetted by KRA as Appointed VAT Withholding Agents** must
withhold it — this is a named, published list, not a general obligation. Reduces the
supplier's output VAT liability for the period.

**They can apply at once.** A KRA-gazetted agent (e.g. a parastatal) paying a VAT-registered
supplier for a WHT-eligible service (professional/management/training fees at 5%, contractual
fees at 3%) owes both, independently, on the same taxable value — confirmed against a real
tax consultant for this app's own rate table (`setup/wht.py`).

Verify current rates, thresholds and the WVAT agent list against KRA guidance before relying
on any default in this app — tax law changes, and nothing here auto-updates against KRA.

## Why two separate mechanisms in this app

ERPNext's own Tax Withholding Category engine (which already powers WHT here) cannot
represent WHT and WVAT together in one category: a category allows only one account per
company (`Tax Withholding Category.validate_companies_and_accounts()`) and only one rate per
date/group (`validate_dates()`), and `Purchase/Sales Invoice Item.tax_withholding_category` is
a single Link field — one line can only carry one category through that engine. Verified
directly against ERPNext v16 source, not assumed.

So: **WHT keeps using ERPNext's existing engine, unchanged.** **VAT Withholding is new and
independent** — a flat rate posted via Payment Entry's own native `deductions` table (account
+ amount), which is what actually lets a single Payment Entry carry both a WHT-driven invoice
adjustment and a VAT Withholding deduction at once.

## Setup

In **Kenyan Accountant Settings**, under "VAT Withholding (WVAT)":

- **We Are a KRA-Appointed VAT Withholding Agent** — off by default. Only tick this once KRA
  has actually gazetted this business. This gates the *payable* side only (see below).
- **VAT Withholding Rate** — defaults to 2%, editable.
- **VAT Withholding Payable to KRA Account** / **VAT Withholding Receivable Account** — seeded
  automatically by `run_setup()`, same as every other account this app manages.

## Direction 1 — this company withholds from its suppliers

Only relevant once "We Are a KRA-Appointed VAT Withholding Agent" is ticked. Per supplier,
tick **Subject to VAT Withholding (WVAT)** on the Supplier record — configurable per supplier
on purpose, not automatic for every VAT-registered supplier, so a business can be selective.

When paying that supplier, use the **Add VAT Withholding** button on the Payment Entry (shown
for a draft payment to a flagged Supplier) to add the deduction at the correct rate, or add a
row to the Deductions table by hand against the VAT Withholding Payable account.

## Direction 2 — a customer withholds from this company

Does **not** depend on this company's own agent status — it's a fact about the customer, not
us. Tick **Withholds Tax From Us (e.g. Parastatal)** on the Customer record. The same
**Add VAT Withholding** button appears on a draft payment received from that customer,
pointed at the Receivable account instead.

For **WHT** withheld by a customer (the more common half of this direction — see above, WHT
obligation is broad, not gazetted-agent-only): assign the relevant WHT category
(`setup/wht.py`'s categories) to the Customer and tick Apply TDS on the Sales Invoice, exactly
as already done on the purchase side. This is not new — `wht_receivable_account` existed in
Kenyan Accountant Settings from the original WHT work; this is simply the first thing to use
it, on the sales side.

## Tracking: Withholding Tax Credit

Every credit from either mechanism — VAT Withholding (from Payment Entry) or WHT (from
Purchase/Sales Invoice) — is mirrored into a **Withholding Tax Credit** record automatically.
Never created by hand; always synced from its source document on submit/cancel.

Fields worth knowing: **Certificate Number** / **Certificate Date** (record what the
withholding party issues), and **Claimed on a Filed Return** — a self-reported checkbox, not
verified against KRA (there is no integration to check that against). The point of this
doctype is visibility: a certificate number sitting in a payment's free-text description is
easy to lose track of; a queryable list of unclaimed credits is not.
