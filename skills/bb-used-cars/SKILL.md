---
name: bb-used-cars
description: >-
  Search and inspect pre-owned and used car inventory across BB Dealerships.
  Prioritizes Dealership & Pre-Owned inventory by default, and searches extended regional branches on instruction.
  Automates vehicle image set downloads and formatted caption outreach.
---

# BB Pre-Owned / Used Car Stock Inventory & Customer Outreach

## Overview
This skill guides stock lookups, inventory sourcing, vehicle image scraping/downloading, and structured WhatsApp outreach across dealership websites.

## Local Inventory Cache & Daily Stock Sheets
All pre-owned inventory for **{DEALERSHIP_NAME}** and **{DEALERSHIP_NAME_ALT}** is dynamically crawled, parsed, and synced daily at 1:00 AM SAST (GMT+2 Johannesburg / 23:00 UTC) without images:
- **Daily Stock Sheet (CSV)**: `{INSTALL_DIR}/jax-shared/data/inventory/stock_sheet.csv`
- **Daily Stock Sheet (JSON)**: `{INSTALL_DIR}/jax-shared/data/inventory/stock_sheet.json`
- **Daily Stock Sheet (Markdown)**: `{INSTALL_DIR}/jax-shared/data/inventory/stock_sheet.md`
- **Metadata Database**: `{INSTALL_DIR}/jax-shared/data/inventory/stock.json`
- **Dynamic Sync Engine**: `python3 {INSTALL_DIR}/jax-shared/scripts/sync_stock.py`
- **Instant Search Script**: `PYTHONPATH=skills/bb-used-cars/scripts python3 skills/bb-used-cars/scripts/search_stock.py --min-price <min> --max-price <max> -q "<query>" [--stock-id <id>] [--vin <vin>] [--mm-code <code>] [--body <style>] [--color <color>]`

## Zero-Friction Availability Query Protocol (MANDATORY)
Whenever {SALESPERSON_NAME} asks for vehicle availability (by model, budget, body style, MM Code, Stock ID, or VIN):
1. **Instant Local Cache Query**: Run `search_stock.py` or inspect `{INSTALL_DIR}/jax-shared/data/inventory/stock.json` immediately (<10ms).
2. **Immediate Availability Presentation**: Present the result directly with zero friction:
   - Model, Year, Floor ({DEALERSHIP_NAME} or {DEALERSHIP_NAME_ALT})
   - Price & Mileage
   - Body style, Transmission, Fuel, Exterior Color
   - Stock ID, MM Code, VIN
   - Listing URL
3. **No Fluff or Web Crawl Delay**: Answer on the fly from cache without re-scraping the web or asking unnecessary questions when the cache has the answer.
4. **Closest Alternatives**: If an exact vehicle is sold or unavailable, immediately present the closest alternative in stock.

## Dealership Hierarchy & Priority

### 1. Primary Dealerships ({DEALERSHIP_NAME} - Always Search First)
Always check the local cache or primary branch sites first for any used vehicle lookup or customer inquiry:
- **{DEALERSHIP_NAME}**: https://dealership.example.com/used/
- **{DEALERSHIP_NAME_ALT}**: https://preowned.example.com/used/

**Search Protocol (Instant & Direct)**:
1. Run `search_stock.py` (or inspect `{INSTALL_DIR}/jax-shared/data/inventory/stock.json`).
2. Filter the vehicles matching the requested model or price bracket.
3. If matching options are found (or even 1 match), immediately present the result.
4. If there are few exact matches, include the closest available alternatives just above the budget and ask if the customer/{SALESPERSON_NAME} wants to see those.
5. **DO NOT** loop through secondary branches or crawl dozens of pages unless {SALESPERSON_NAME} explicitly asks to "expand search to all regional branches" or "check other dealerships".

### 2. Extended Regional Dealerships (Search Only When Instructed)
When {SALESPERSON_NAME} explicitly requests expanding the search across all regional pre-owned stock or neighboring branches, check:
- **Regional Branch 1**: https://branch1.example.com/used/
- **Regional Branch 2**: https://branch2.example.com/used/
- **Regional Branch 3**: https://branch3.example.com/used/
- **Regional Branch 4**: https://branch4.example.com/used/
- **{DEALERSHIP_NAME_SECONDARY}**: https://branch.example.com/used
- **Regional Branch 5**: https://branch5.example.com/used/
- **Regional Branch 6**: https://branch6.example.com/used/
- **Regional Branch 7**: https://autotrader.example.com/dealer/branch/12345
- **Regional Branch 8**: https://branch8.example.com/used/

## Image Scraping & DOM Cleansing Invariant (CRITICAL)
When fetching or downloading listing photos from dealership web pages:
- **DOM Cleansing (MANDATORY)**: Always decompose/remove all sidebar widgets, similar car recommendations, headers, and footers (`aside, .stm_similar_cars, .stm-similar-cars-units, footer, header, .widget, .similar-cars`) before parsing images.
- **Gallery Scope**: Extract images strictly from `.mosaic-gallery` / `.motors-elementor-single-listing-gallery-mosaic`.
- **Proxy Fallback**: VM direct outbound connections to dealership hosts time out. Image downloads must route through `https://wsrv.nl/?url=`.

## Multi-Image Customer Dispatch Protocol (MANDATORY)
When tasked with sending vehicle options or vehicle photos to a customer or to {SALESPERSON_NAME}:
1. **Retrieve / Download Gallery (MANDATORY)**: Run:
   `PYTHONPATH=skills/bb-used-cars/scripts python3 skills/bb-used-cars/scripts/fetch_listing_images.py "<listing_url_or_slug>"`
   This retrieves the clean, pre-cached local gallery from `{INSTALL_DIR}/jax-shared/data/inventory/vehicles/<slug>` instantly (< 10ms).
2. **First Image Caption (MANDATORY)**: The very first image sent MUST have the single-line summary description in its text:
   `[YEAR] [MAKE] [MODEL] [VARIANT] | Mileage: [MILEAGE]KM | from R[PRICE]`
   Example: `2024 SUZUKI S-PRESSO 1.0 GL 5MT | Mileage: 44869KM | from R155,900`
3. **Dispatch Tag (MANDATORY)**: At the very end of your response, ALWAYS append:
   `[SEND_GALLERY: <output_dir_path>]`
   Example: `[SEND_GALLERY: {INSTALL_DIR}/jax-shared/data/inventory/vehicles/suzuki-s-presso-1-0-gl-5mt-2024]`
   The WhatsApp/Telegram system will automatically detect this tag, send the first image with your vehicle caption, and deliver all remaining gallery photos sequentially to create a clean photo album!

## Multi-Option WhatsApp Proposal Template
When presenting a list of multiple vehicle options before sending full photo albums:

Good day [Customer Name], this is {SALESPERSON_NAME} from {DEALERSHIP_NAME}! 🚗✨

Here are quality pre-owned options currently available matching what you are looking for:

Option 1:
- Year & Model: [Year] [Description / Variant]
- Mileage: [Mileage] km
- Price: R[Price]
- Details & Photos: [Link]

Option 2:
- Year & Model: [Year] [Description / Variant]
- Mileage: [Mileage] km
- Price: R[Price]
- Details & Photos: [Link]

Which of these suits you best, or would you like to arrange a quick test drive with me today? 😊
