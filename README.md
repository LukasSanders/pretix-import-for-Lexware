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
### Dependencies
The script needs the modules PyYAML, Requests and Rich. You can install the dependencies with `pip install -r requirements.txt`. 
### Prerequisites
The script only imports invoices from pretix, so make sure that the creation of invoices is activated in the event settings in any case and for every type of customer. Please refer to the [pretix documentation](https://docs.pretix.eu/tutorial/continuous-operation/payment/?h=invoices#setting-up-invoices) for further instructions. 

In Lexware, you have to generate a key for the Lexware Public API at (https://app.lexware.de/addons/public-api). Please refer to the [API documentation](https://developers.lexware.io/docs/#lexware-api-documentation-introduction) for further information. 

In pretix, you have to generate an API token in the team settings and read the API base URL (host, organiser, event). *Mind that upper and lower case letters are taken into account for organiser and event keys.* Please refer to the [documentation](https://docs.pretix.eu/dev/api/tokenauth.html#obtaining-an-api-token) for further instructions. 
### Settings
The script reads its settings and API keys from a settings file which is by default `settings.yaml` in the same directory. The settings file can also be stored in a different location or have a different name; in that case, file path and file name has to be specified using the command-line argument `-s, --settings-file` (see below).

Before the first use, you have to check and if necessary adjust the settings as well as fill in the API tokens for Lexware and pretix. 
#### API Settings
The lexware API url remains static, the pretix URL has to be adjusted according to your host, organiser and event. 
Enter the API tokens for both APIs. Please refer to the API documentation on how to generate an API token. 

*Important:* After you have entered the API tokens, make sure to store the settings file in a secure location. Do not publish it to any code repositories or anywhere else online. The repository includes a pre-commit hook that blocks commits if the staged `settings.yaml` contains real credentials. Enable it once per clone with `git config core.hooksPath .githooks`. It requires Python and PyYAML to be available. The check is an accidental-commit safeguard and can be bypassed with `git commit --no-verify`.

Lexware uses posting categories to classify bookkeeping documents. In the API settings, you can specify a posting category ID which will be used for all vouchers created by this script. By default, it is `sale of goods`. 
You can adjust this category according to your needs if necessary. You can find a list of popular categories in the [API documentation](https://developers.lexware.io/docs/#vouchers-endpoint-list-of-categoryids) or use the [respective endpoint](https://developers.lexware.io/docs/#posting-categories-endpoint) to retrieve a full list. *Please mind that creating sales invoices and sales credit notes will not work with all posting categories.* 

The Lexware API has a [rate limit of two requests per second](https://developers.lexware.io/docs/#api-rate-limits). If the rate limit is exceeded, a HTTP response code 429 will be returned. In that case, this script will wait for two seconds and retry the API call. 

You can adjust the number of times the script should retry the API call before failing. Please note that this only applies to errors with the HTTP status code 429 and not other errors which will lead to an immediate fail of the respective request. 
#### Text Settings
Changing the settings under `texts`, you can adjust the text which is added as a [voucher remark](https://developers.lexware.io/docs/#vouchers-endpoint-voucher-properties): the general text, an additional text for cancellations, an additional text if a reference document is given, and a text for vouchers which represent the usage of a gift card. Placeholders which refer to properties of the respective pretix API endpoints can be used. For more information, please read the respective section below.
### Running the script
The script can be called with the following command line arguments:

-d, --date DATE        Date for which to import invoices (format: YYYY-MM-DD).
-t, --test             Run in test mode without actually creating invoices.
-l, --logging          Create a log file in the same directory. 
-s, --settings-file    Path to the YAML settings file (defaults to ./settings.yaml).

If the import is handled manually on an irregular basis, pass the date (format: YYYY-MM-DD) for which to import invoices. The argument may be omitted if invoices for yesterday are to be imported. If the script is run automatically (e.g. via cron job), it is advisable to omit the date argument and run the script daily, as it will then always import invoices for the 
previous day.

As the script imports invoices for a whole day, it should not be run for the current day. A failsafe for 
duplicate imports is not yet implemented. 

Use `-s` or `--settings-file` to load a configuration file with a different file name and/or from a different location, for example:
`python import_invoices.py --settings-file path/to/settings.yaml`. You can also use this to run the script for several different pretix events. 

For testing purposes, the script can be run in test mode, which will not create any vouchers in Lexware. Real data is fetched from pretix though. Test mode
can be activated passing the respective command line argument (-t, --test). 
In test mode, the script will still fetch and convert the invoices, just not call the Lexware API. 

The script will use [Rich](https://github.com/Textualize/rich) for output to the console. If the script is run in background mode or if a log is necessary for any other reason, logging to a file in the same directory can be activated using the command line argument -l resp. --logging. The logging file will be created and edited using RotatingFileHandler. 

## API References
For further information on the pretix and Lexware APIs, please refer to the following documentation:
- https://developers.lexware.io/docs/
- https://docs.pretix.eu/dev/api/index.html

## Voucher Texts 
All vouchers (see [endpoint documentation](https://developers.lexware.io/docs/#vouchers-endpoint)) created in lexware will get a descriptive text (property "remark"). This text should give accountants, controllers or auditors a sufficient description of the nature and origin of the voucher resp. accounting document. It is also used for allocating bank transfers and it is searchable, so it is generally a good idea to add customer information or other reference numbers like the order number for clearing purposes. 

The voucher text is configured under `texts` in the settings file: 
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
