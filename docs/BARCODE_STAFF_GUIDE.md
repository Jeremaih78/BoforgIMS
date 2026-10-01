# Barcode & serialized inventory guide

## Three identifiers

**Product barcode** identifies a model or consumable. Keep a useful manufacturer barcode and assign it to its existing product. A product can have several alternate barcodes. Generate a Boforg product code only when needed.

**Boforg Unit ID**, such as `BF-U-000000123`, identifies one physical machine permanently. Every serialized machine receives one, even when a manufacturer serial is available.

**Manufacturer serial** is optional information from the machine's plate. It is different from the product barcode. Never enter the shared model barcode as every machine's serial.

## Connect and test the scanner

1. Connect the USB cable or wireless receiver. Configure keyboard/HID mode and an Enter suffix using the scanner's own manual.
2. Open **Scan / Search → Scanner test**. Click the input and scan a known label.
3. Confirm the displayed characters, including leading zeroes. If Enter does not submit, configure the suffix or press the button manually.

The browser observes keyboard input; it cannot certify the scanner model or wireless connection. Manual typing works too. Phone-camera scanning and offline stock changes are not included.

## Assign or generate a product barcode

Open **Inventory → Barcodes & units** beside the product. Scan the manufacturer barcode and choose **Assign barcode**. Use **Generate Boforg barcode** for products with no useful existing code. Repeating generation returns the same code.

An unknown scan offers authorized managers **Assign this barcode to an existing product**. Search the product, select it, and confirm. Return to the original tab and scan again. Assignment never creates stock.

Managers control barcode assignment. If a code is already used or ambiguous, stop and review the conflicting records. Do not attach the same barcode to another product.

## Enable individual tracking

For a new machine product, choose **Serial Numbers** tracking, enable inventory tracking, and start quantity at zero. Configure warranty days if appropriate; zero means no specified warranty.

For existing stock, a manager verifies the physical count and opens **Identify existing stock** on the product identity page. Resolve reservations first, enter the location and count-confirmation reason, and confirm. IMS creates the missing physical identities without adding the quantity again. Print and attach the Unit IDs immediately. Do not use this action to conceal an unexplained stock difference.

## Receive machines

1. Create the shipment and its product lines through the existing receiving workflow. Product and shipment tracking modes must agree.
2. Once the shipment is arrived/cleared, open **Receive**. Enter the storage location and receipt quantity.
3. Scan the optional manufacturer serial into each unit row. Enter moves to the next row. Leave rows blank for machines without serials. Bulk paste is also available.
4. Keep **Generate Boforg Unit IDs for units without manufacturer serials** checked when serials are missing. Duplicate serials are rejected.
5. Submit the receipt. All shipment lines must be fully received under the existing shipment rule. Quantity, physical units, landed cost and receipt accounting commit together.
6. Use the shipment's unit-label link, select units, and print labels. The database assigns final IDs at receipt; unused preview IDs are not reserved.

## Print labels

Use **Print selected labels** on Scan / Search, a unit passport's **Print / reprint label**, or a product barcode's **Print label**. Unit labels support A4 sheets (12 stickers, 94 × 43 mm each, with dashed cutting outlines; 97 mm horizontal and 45 mm vertical pitch) and 100 × 50 mm thermal stock. Use **Select all matching units (all pages)** to print the entire filtered result, **Select this page** for its 30 visible units, or tick individual labels. The live counter shows how many will print. Select all respects search, status, location and shipment filters. Each PDF supports up to 1,000 labels; narrow the filters for larger inventories. Clear selection resets the choices. Deselecting an individual label exits all-pages selection and keeps only the remaining checks on the visible page.

Print at **100% / actual size**, not Fit. Trial-print on plain paper before adhesive stock. Check your printer's margins and label pitch. For plain A4 adhesive sheets, cut along the dashed sticker outlines; instructions and sheet numbers are outside the stickers. Do not cover the manufacturer's plate. Labels contain Code 128 and QR versions of the same identifier. QR contains the ID, not a public customer/warranty page. PDF export/reprint is audited; IMS cannot confirm paper physically left the printer.

## Scan into invoices and quotations

Create the document and select its customer as usual. In the scanner field:

- A quantity product barcode adds one item. Another deliberate scan adds another; a retry of an unconfirmed request does not.
- A machine's Unit ID or unique manufacturer serial identifies and reserves that machine for an invoice. Scanning it again says it is already assigned.
- A serialized product barcode asks you to scan/select the physical unit. It does not choose a machine silently.
- A matching unassigned combo component is filled without adding another component or changing its bundle discount.
- Quotations add the product without reserving a physical machine. After conversion, assign units on the invoice.

Wait for the response before scanning the next item. Keep-focus and sound are optional. If the connection fails, use **Retry the same scan**. Do not assume a timeout means it was not saved. Paid/finalized invoices cannot be changed; partially paid invoices retain the existing editing restrictions. Full payment finalizes stock through the existing payment service. Invoice printouts include assigned/sold Unit IDs, even after a returned unit is later resold.

Online shop orders reserve exact available machines. Failed pending orders release them; successful payment records their sale. A late payment after released units needs staff reconciliation instead of silently selling another machine.

## Locations, stocktake and discrepancies

Use a unit passport's **Move location** with a destination and reason. Reserved or sold machines cannot be moved through this action.

Start **Stocktakes → New count**. Leave location blank for all units and quantity stock; specifying a location counts serialized units only. Scan each machine once. Duplicate unit scans do not increase its count. Unknown and unexpected scans are visible; neither creates inventory.

Quantity items can be scanned repeatedly or counted in boxes using **Enter / correct a bulk quantity count**. Enter the actual total, not an increment, and record the method/reason. Counts do not change inventory until approval.

Finish counting and submit for review. An authorized approver chooses **Keep records / follow up** or **Apply count** for every discrepancy and enters a reason. Applying a missing available machine writes it off. Reserved/unexpected machines must use their normal reservation/return/location workflow. If stock changed while counting, approval stops: keep the old session as evidence and start a new count. Export the variance CSV for follow-up. Coordinate counts during a quiet period.

## Returns, warranty and repairs

Scan the Unit ID to open its passport and sales/warranty history. A manager can receive a sold return or open a warranty/repair case. Returned machines enter quarantine and are not added to saleable stock.

Record the fault, inspection, repair and resolution. Close the service case before approving restock. After closing, either return the repaired unit to its original customer, restock an inspected unit, return it to the supplier, or write it off with a reason.

A case can issue an available replacement of the same product. This consumes one unit and links it to the original sale/customer, preserving the original warranty expiry. It does not create another invoice charge or issue a refund. Do not hand out a second replacement by creating duplicate cases.

Financial refunds, credit notes, warranty expense postings and write-off journals remain a separate authorized accounting process. Inventory actions record quantities and reasons; they do not invent those financial documents.

## History and troubleshooting

**Scan / Search** finds a product or exact machine. Filter units by product/SKU, status, location or shipment; export CSV if needed. Passports retain receipt/adoption, assignment, sale, return, repair, movement and label-export activity. Older activity is paginated.

- **Unknown barcode:** confirm all digits, then ask a manager to assign it. Do not receive inventory merely to make a lookup work.
- **Product found, machine required:** scan the Unit ID, not its shared product code.
- **Already reserved/sold/faulty:** inspect the passport. Do not relabel the machine to bypass its status.
- **Stock count differs from units:** ask a manager to reconcile source records and physical stock.
- **Manufacturer serial typo:** manager uses **Correct manufacturer serial**, with a reason. Unit ID remains unchanged.
- **Scanner types into another field:** click the scanner input. The app deliberately does not intercept ordinary typing globally.
- **No label printing permission:** ask the administrator for `inventory.print_inventory_labels`.
- **Browser refresh after uncertain scan:** retry the retained request before scanning another item. There is no offline transaction queue.

Physical scanner/printer calibration must be tested on Boforg's actual equipment before rollout.
