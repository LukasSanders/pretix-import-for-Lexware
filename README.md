# pretix Import for Lexware
Import invoices from a pretix ticket shop to Lexware. 

## Summary
Import all ticket sales from pretix and post them as vouchers to Lexware. The script imports invoices for 
a specified date which can be passed as a command-line argument (default: yesterday). 
In Lexware, a voucher (not an actual invoice) will be created using the collective contact. All lines of 
the invoice will be passed as voucher items, so different tax rates will be handled correctly. 
Cancellation invoices will also be imported as sales credit notes. 
All vouchers will be creating using the same posting category which is specified as a constant in the script. 

## Setup & Use
Install the dependencies with `pip install -r requirements.txt`. The script reads its non-secret settings from `settings.yaml` in the same directory. API URLs and retry count are grouped under `api_settings.lexware` and `api_settings.pretix`; voucher text templates are under `texts`.

The API keys are read from environment variables. To load them from a `.env` file, copy `env.sample` to `.env` and add your keys:
- PRETIX_API_KEY: The API key for the pretix API.
- LEXWARE_API_KEY: The API key for the Lexware API.

The settings under `texts` in `settings.yaml` adjust the [voucher remark](https://developers.lexware.io/docs/#vouchers-endpoint-voucher-properties): the general text, additional cancellation text, reference text, and gift-card text. Placeholders which refer to properties of the [pretix invoice endpoint](https://docs.pretix.eu/dev/api/resources/invoices.html) can be used. For more information, please read the respective section below.

The Lexware API URL remains static, the pretix API URL is event-specific and must be set accordingly. 
You will need to run this script multiple times for each event if you want to handle multiple events.
The API keys for both APIs must be generated in the respective systems. For more information on URLs and on how to 
generate the API keys, please refer to the respective API documentation (see below).

As the script imports invoices for a whole day, it should not be run for the current day. A failsafe for 
duplicate imports is not yet implemented. 

The script can be called with the following command line arguments:
-d, --date DATE        Date for which to import invoices (format: YYYY-MM-DD).
-t, --test             Run in test mode without actually creating invoices.
-l, --logging          Create a log file in the same directory. 
-s, --settings-file    Path to the YAML settings file (defaults to ./settings.yaml).

Use `-s` or `--settings-file` to load a configuration file from a different location, for example:
`python import_invoices.py --settings-file path/to/settings.yaml`.

If the import is handled manually, pass the date (format: YYYY-MM-DD) for which to import invoices. The argument
may be omitted if invoices for yesterday are to be imported. If the script is run automatically (e.g. via cron job),
it is advisable to omit the date argument and run the script daily, as it will then always import invoices for the 
previous day.

For testing purposes, the script can be run in test mode, which will not create any vouchers in Lexware. Test mode
can be activated passing the respective command line argument (-t, --test). 
In test mode, the script will still fetch and convert the invoices, just not call the Lexware API. 

The script will use [Rich](https://github.com/Textualize/rich) for output to the console. If the script is run in background mode or if a log is necessary for any other reason, logging to a file in the same directory can be activated using the command line argument -l resp. --logging. The logging file will be created and edited using RotatingFileHandler. 

## API References
For further information on the pretix and Lexware APIs, please refer to the following documentation:
- https://developers.lexware.io/docs/
- https://docs.pretix.eu/dev/api/index.html

## Voucher Texts 
All vouchers (see [endpoint documentation](https://developers.lexware.io/docs/#vouchers-endpoint)) created in lexware will get a descriptive text (property "remark"). This text should give accountants, controllers or auditors a sufficient description of the nature and origin of the voucher resp. accounting document. It is also used for allocating bank transfers and it is searchable, so it is generally a good idea to add customer information or other reference numbers like the order number for clearing purposes. 

The voucher text is configured under `texts` in `settings.yaml`:
- `voucher`: The general voucher text which is added to all requests.
- `cancellation`: Additional text which will be added *in front of* the general text in case the invoice is a credit note/cancelation.
- `reference`: Additional text which will be added *at the end of* the general text in case the invoice refers to another document.
These settings can include *placeholders* (see below). It is possible to include line breaks.

# Default Voucher Texts
- `VOUCHER_TEXT`: 'Order %order% for event %event%,\n %invoice_to_name%, %invoice_to_street%, %invoice_to_zipcode% %invoice_to_city%, %invoice_to_country%' which includes the order number as well as the customer address
- `VOUCHER_ADDT_CANCELATION`: 'Cancelation '
- `VOUCHER_ADDT_REFERENCE`: 'Reference: %refers%' which includes the reference document number

Thus, the default voucher text for a cancellation (which refers to the cancelled invoice) would be for example: 

```
Cancelation
Order A1B2C3 for event TestCon 2027,
John Doe, 12 Testing Crescent, AB3 C4D Testham, GB
Reference: TESTCON2027-2026-000123
```

# Placeholders 
All properties of the [invoice endpoint](https://docs.pretix.eu/dev/api/resources/invoices.html) of the pretix API can be used as placeholders as long as they are direct child properties and have the type *String* or are at least convertible to a string value. To add a property, the name in percent signs (e.g. *%invoice_to%*) can be added to the text. 

Please mind: 
1. It is not possible to add nested properties or paths like *%lines/description% or anything in that direction. The respective functions simply uses the placeholder as a key to read the respective value from the invoice JSON object.
2. When the property is technically not convertible to a string value, the script will fail. Make sure to only use string values or values which can be converted to a string.
3. When the property is technically convertible to a string, but the conversion leads to an undesirable output (e.g. using boolean, datetime or array/list values), it is not recommended to use that property.
4. It is not possible to apply formatting or anything in that direction.
5. The name of the property has to be written correctly including proper case. Please look the respective key names up in the [API documentation](https://docs.pretix.eu/dev/api/resources/invoices.html). 
