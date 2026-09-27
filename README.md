### Kenya Accounting (`kenyan_accountant`)

Kenya Chart of Accounts, VAT and WHT configuration for ERPNext

### Documentation

- [WHT and VAT Withholding (WVAT)](docs/vat-withholding.md)

### Printed documents

Standard print formats for Sales Invoice (prints as Tax Invoice / Invoice / Credit
Note), Quotation, Purchase Order, Purchase Invoice and Payment Entry (Payment Receipt /
Voucher). They print the Company's logo, address and KRA PIN (or the client's own
Letter Head), and the payment details and accent colour from Kenya Accounting
Settings > Printed Documents.

They become each doctype's default once, at install. A client who sets another
format as default keeps it through every update; a client who wants a different
layout duplicates ours (standard formats can't be edited in place), and fixes to
ours reach everyone still using them.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app kenyan_accountant
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/kenyan_accountant
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
