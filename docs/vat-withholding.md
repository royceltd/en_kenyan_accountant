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

ERPNext's own Tax Withholding Category engine (which already powers WHT withheld *by* this
company on Purchase Invoice) cannot represent WHT and WVAT together in one category: a
category allows only one account per company (`Tax Withholding Category.
validate_companies_and_accounts()`) and only one rate per date/group (`validate_dates()`), and
`Purchase Invoice Item.tax_withholding_category` is a single Link field — one line can only
carry one category through that engine. Verified directly against ERPNext v16 source, not
assumed.

So VAT Withholding is a new, independent mechanism: a flat rate posted via Payment Entry's
own native `deductions` table (account + amount), which is what lets a single Payment Entry
carry both a WHT-driven invoice adjustment and a VAT Withholding deduction at once.

**A design mistake made and corrected while building this, worth knowing about**: the first
version tried to handle "a customer withheld WHT from us" by mirroring the purchase side —
assigning a WHT category to the Customer and ticking Apply TDS on the Sales Invoice, the same
pattern as Purchase Invoice. This was wrong, found by actually submitting a real Sales Invoice
and reading the resulting GL Entries: ERPNext's matching mechanism there is
`SalesTaxWithholding`, whose own docstring names it **"(TCS)"** — Tax Collected at Source, an
Indian regime where the *seller* collects an *additional* tax from the buyer. That's the
opposite of a customer withholding tax from what they owe us. It posted the withheld amount as
a *credit* to the configured account (correct for a TCS liability, wrong for a receivable
asset) and, on a second attempt, threw a GL balance-direction error mid-submit. Kenya has no
TCS-equivalent tax, so there was never a legitimate use for that mechanism here. **WHT
withheld from this company is instead recorded the same way as VAT Withholding** — see
Direction 2 below.

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
for a draft payment to a flagged Supplier) to add the deduction at the correct rate. It also
reduces `Paid Amount` and sets the deduction's sign correctly so `Difference Amount` balances
to zero — do this through the button rather than by hand; the sign convention for a "Pay"
entry is genuinely counter-intuitive (confirmed against ERPNext's own
`set_difference_amount()` source, not guessed) and easy to get backwards.

WHT this company withholds from a supplier needs no separate step here — it still works
through the existing WHT categories on Purchase Invoice (Apply TDS), unchanged.

## Direction 2 — a customer withholds from this company

Does **not** depend on this company's own agent status — it's a fact about the customer, not
us. Tick **Withholds Tax From Us (e.g. Parastatal)** on the Customer record.

Recording a payment from that customer, two buttons are available:

- **Add VAT Withholding** — same as Direction 1, computed automatically at the configured
  rate, posted to the Receivable account instead of Payable.
- **Add WHT Withheld** — no computed rate here, since WHT's rate depends on the payment type
  (5% professional/consultancy, 3% contractual, ...), not one flat percentage. Enter the
  amount straight from the withholding certificate the customer provides, with an optional
  certificate number.

Both buttons correctly adjust `Received Amount` and the deduction's sign — for a "Receive"
entry the sign convention is the mirror image of "Pay", and both were verified end-to-end
against real submitted documents (GL Entries checked directly, not assumed from a clean submit
alone) before shipping.

## Tracking: Withholding Tax Credit

Every credit is mirrored into a **Withholding Tax Credit** record automatically — never
created by hand, always synced from its source document on submit/cancel. WHT withheld *by*
this company syncs from the Purchase Invoice; everything else (VAT Withholding in either
direction, and WHT withheld *from* this company) syncs from the Payment Entry.

Fields worth knowing: **Certificate Number** / **Certificate Date** (record what the
withholding party issues), and **Claimed on a Filed Return** — a self-reported checkbox, not
verified against KRA (there is no integration to check that against). The point of this
doctype is visibility: a certificate number sitting in a payment's free-text description is
easy to lose track of; a queryable list of unclaimed credits is not.
