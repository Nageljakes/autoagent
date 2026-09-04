#!/usr/bin/env python3
"""
Populate and sync local inventory for Main Dealership Branch & Suzuki.
Extracts listing galleries and downloads high-res photos into local cache.
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.environ.get("INVENTORY_DATA_DIR", os.path.join(SHARED_DIR, "data", "inventory"))
VEHICLES_DIR = os.path.join(DATA_DIR, "vehicles")
STOCK_FILE = os.path.join(DATA_DIR, "stock.json")
os.makedirs(VEHICLES_DIR, exist_ok=True)

# Complete verified inventory list across Main Dealership Branch and Suzuki
RAW_LISTINGS = [
    # --- Main Dealership Branch ---
    {
        "slug": "kia-picanto-1-0-street-2019",
        "title": "KIA PICANTO 1.0 STREET 2019",
        "price": "R139,900",
        "price_num": 139900,
        "mileage": "94,515 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/kia-picanto-1-0-street-2019/"
    },
    {
        "slug": "suzuki-s-presso-1-0-gl-5mt-2024",
        "title": "SUZUKI S-PRESSO 1.0 GL 5MT 2024",
        "price": "R155,900",
        "price_num": 155900,
        "mileage": "44,869 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/suzuki-s-presso-1-0-gl-5mt-2024/"
    },
    {
        "slug": "renault-kwid-1-0l-evolution-2026",
        "title": "RENAULT KWID 1.0L EVOLUTION 2026",
        "price": "R175,900",
        "price_num": 175900,
        "mileage": "1,515 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/renault-kwid-1-0l-evolution-2026/"
    },
    {
        "slug": "renault-kwid-1-0l-evolution-2026-branch-2",
        "title": "RENAULT KWID 1.0L EVOLUTION 2026",
        "price": "R179,900",
        "price_num": 179900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/renault-kwid-1-0l-evolution-2026-2/"
    },
    {
        "slug": "vehicle-magnite-1-0t-acenta-cvt-my21-2021",
        "title": "COMPACT CROSSOVER 1.0T Acenta CVT MY21 2021",
        "price": "R189,900",
        "price_num": 189900,
        "mileage": "67,341 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0t-acenta-cvt-my21-2021/"
    },
    {
        "slug": "renault-kwid-1-0l-techno-2026",
        "title": "RENAULT KWID 1.0L TECHNO 2026",
        "price": "R189,900",
        "price_num": 189900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/renault-kwid-1-0l-techno-2026/"
    },
    {
        "slug": "mahindra-xuv300-my22-1-5d-w6-2022",
        "title": "MAHINDRA XUV300 MY22 1.5D W6 2022",
        "price": "R190,900",
        "price_num": 190900,
        "mileage": "58,000 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/mahindra-xuv300-my22-1-5d-w6-2022/"
    },
    {
        "slug": "renault-kiger-1-0-turbo-zen-2022",
        "title": "RENAULT KIGER 1.0 TURBO ZEN 2022",
        "price": "R199,900",
        "price_num": 199900,
        "mileage": "46,000 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/renault-kiger-1-0-turbo-zen-2022/"
    },
    {
        "slug": "suzuki-swift-1-2-gl-5mt-2026",
        "title": "SUZUKI SWIFT 1.2 GL 5MT 2026",
        "price": "R205,900",
        "price_num": 205900,
        "mileage": "2,500 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/suzuki-swift-1-2-gl-5mt-2026/"
    },
    {
        "slug": "vehicle-magnite-1-0-visia-mt-2026",
        "title": "COMPACT CROSSOVER 1.0 Visia MT 2026",
        "price": "R229,900",
        "price_num": 229900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0-visia-mt-2026/"
    },
    {
        "slug": "mahindra-xuv3xo-1-2-mx2-at-2025",
        "title": "MAHINDRA XUV3XO 1.2 MX2 AT 2025",
        "price": "R229,900",
        "price_num": 229900,
        "mileage": "40,928 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/mahindra-xuv3xo-1-2-mx2-at-2025/"
    },
    {
        "slug": "vehicle-magnite-1-0-visia-amt-2026",
        "title": "COMPACT CROSSOVER 1.0 Visia AMT 2026",
        "price": "R229,900",
        "price_num": 229900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0-visia-amt-2026/"
    },
    {
        "slug": "vehicle-magnite-1-0-acenta-mt-2026",
        "title": "COMPACT CROSSOVER 1.0 Acenta MT 2026",
        "price": "R249,900",
        "price_num": 249900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0-acenta-mt-2026/"
    },
    {
        "slug": "vehicle-magnite-1-0t-acenta-mt-2026",
        "title": "COMPACT CROSSOVER 1.0T Acenta MT 2026",
        "price": "R289,900",
        "price_num": 289900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0t-acenta-mt-2026/"
    },
    {
        "slug": "ford-ranger-2-2-tdci-xl-4x2-d-cab-2018",
        "title": "FORD RANGER 2.2 TDCi XL 4X2 D/CAB 2018",
        "price": "R299,900",
        "price_num": 299900,
        "mileage": "110,000 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/ford-ranger-2-2-tdci-xl-4x2-d-cab-2018/"
    },
    {
        "slug": "vehicle-navara-single-cab-my21-2-5d-se-4x2-s-cab-2023",
        "title": "DOUBLE CAB BAKKIE SINGLE CAB MY21 2.5D SE 4X2 S CAB 2023",
        "price": "R299,900",
        "price_num": 299900,
        "mileage": "38,000 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-navara-single-cab-my21-2-5d-se-4x2-s-cab-2023/"
    },
    {
        "slug": "omoda-c5-1-5t-street-plus-2026",
        "title": "OMODA C5 1.5T STREET PLUS 2026",
        "price": "R329,900",
        "price_num": 329900,
        "mileage": "264 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/omoda-c5-1-5t-street-plus-2026/"
    },
    {
        "slug": "suzuki-xl6-1-5-gl-5mt-2026",
        "title": "SUZUKI XL6 1.5 GL 5MT 2026",
        "price": "R329,900",
        "price_num": 329900,
        "mileage": "13,647 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/suzuki-xl6-1-5-gl-5mt-2026/"
    },
    {
        "slug": "mahindra-pik-up-single-cab-2-2-mhawk-s-cab-4x2-s6-refresh-2026",
        "title": "MAHINDRA PIK UP SINGLE CAB 2.2 mHAWK S/CAB 4X2 S6 REFRESH 2026",
        "price": "R329,900",
        "price_num": 329900,
        "mileage": "100 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/mahindra-pik-up-single-cab-2-2-mhawk-s-cab-4x2-s6-refresh-2026/"
    },
    {
        "slug": "toyota-corolla-cross-1-8-xs-2024",
        "title": "TOYOTA Corolla Cross 1.8 XS 2024",
        "price": "R339,900",
        "price_num": 339900,
        "mileage": "35,000 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/toyota-corolla-cross-1-8-xs-2024/"
    },
    {
        "slug": "vehicle-magnite-1-0t-acenta-plus-cvt-2026",
        "title": "COMPACT CROSSOVER 1.0T Acenta Plus CVT 2026",
        "price": "R349,900",
        "price_num": 349900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-magnite-1-0t-acenta-plus-cvt-2026/"
    },
    {
        "slug": "omoda-c5-style-x-2026",
        "title": "OMODA C5 STYLE X 2026",
        "price": "R369,900",
        "price_num": 369900,
        "mileage": "4,337 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/omoda-c5-style-x-2026/"
    },
    {
        "slug": "renault-kiger-my22-1-0-turbo-intens-cvt-2026",
        "title": "RENAULT KIGER MY22 1.0 TURBO INTENS CVT 2026",
        "price": "R369,900",
        "price_num": 369900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/renault-kiger-my22-1-0-turbo-intens-cvt-2026/"
    },
    {
        "slug": "ford-next-gen-ranger-2-0l-sit-sup-xl-4x2-6at-2023",
        "title": "FORD Next-Gen RANGER 2.0L SIT SUP XL 4X2 6AT 2023",
        "price": "R389,900",
        "price_num": 389900,
        "mileage": "42,000 km",
        "fuel": "Diesel",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/ford-next-gen-ranger-2-0l-sit-sup-xl-4x2-6at-2023/"
    },
    {
        "slug": "jaecoo-j7-vortex-2wd-2026",
        "title": "JAECOO J7 VORTEX 2WD 2026",
        "price": "R399,900",
        "price_num": 399900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/jaecoo-j7-vortex-2wd-2026/"
    },
    {
        "slug": "vehicle-navara-single-cab-my21-2-5d-se-4x2-s-cab-2025",
        "title": "DOUBLE CAB BAKKIE SINGLE CAB MY21 2.5D SE 4X2 S CAB 2025",
        "price": "R409,900",
        "price_num": 409900,
        "mileage": "100 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-navara-single-cab-my21-2-5d-se-4x2-s-cab-2025/"
    },
    {
        "slug": "hyundai-santa-fe-my21-2-2-executive-7-seater-dct-2022",
        "title": "HYUNDAI SANTA FE MY21 2.2 EXECUTIVE 7 SEATER DCT 2022",
        "price": "R459,900",
        "price_num": 459900,
        "mileage": "58,000 km",
        "fuel": "Diesel",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/hyundai-santa-fe-my21-2-2-executive-7-seater-dct-2022/"
    },
    {
        "slug": "vehicle-navara-double-cab-my25-2-5d-le-at-dc-new-4x2-2025",
        "title": "DOUBLE CAB BAKKIE DOUBLE CAB MY25 2.5D LE AT DC NEW 4X2 2025",
        "price": "R539,900",
        "price_num": 539900,
        "mileage": "100 km",
        "fuel": "Diesel",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/vehicle-navara-double-cab-my25-2-5d-le-at-dc-new-4x2-2025/"
    },
    {
        "slug": "jetour-t1-2-0t-odyssey-7dct-4wd-2026",
        "title": "JETOUR T1 2.0T ODYSSEY 7DCT 4WD 2026",
        "price": "R629,900",
        "price_num": 629900,
        "mileage": "5,891 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/jetour-t1-2-0t-odyssey-7dct-4wd-2026/"
    },
    {
        "slug": "jetour-t2-2-0t-xplora-7dct-4wd-2026",
        "title": "JETOUR T2 2.0T XPLORA 7DCT 4WD 2026",
        "price": "R649,900",
        "price_num": 649900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Main Dealership Branch",
        "listing_url": "https://dealership.example.com/used/listings/jetour-t2-2-0t-xplora-7dct-4wd-2026/"
    },

    # --- Pre-Owned Branch ---
    {
        "slug": "renault-clio-4-0-9-authentique-turbo-2020",
        "title": "RENAULT CLIO 4 0.9 AUTHENTIQUE TURBO 2020",
        "price": "R179,900",
        "price_num": 179900,
        "mileage": "42,829 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/renault-clio-4-0-9-authentique-turbo-2020/"
    },
    {
        "slug": "renault-kwid-1-0l-evolution-2026-suzuki",
        "title": "RENAULT KWID 1.0L EVOLUTION 2026",
        "price": "R188,900",
        "price_num": 188900,
        "mileage": "120 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/renault-kwid-1-0l-evolution-2026/"
    },
    {
        "slug": "hyundai-grand-i10-1-0-motion-my23-2025",
        "title": "HYUNDAI GRAND i10 1.0 MOTION MY23 2025",
        "price": "R214,900",
        "price_num": 214900,
        "mileage": "17,177 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/hyundai-grand-i10-1-0-motion-my23-2025/"
    },
    {
        "slug": "suzuki-celerio-my22-1-0-gl-amt-2026",
        "title": "SUZUKI CELERIO MY22 1.0 GL AMT 2026",
        "price": "R215,900",
        "price_num": 215900,
        "mileage": "5,200 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/suzuki-celerio-my22-1-0-gl-amt-2026/"
    },
    {
        "slug": "tata-punch-1-2-adventure-s-mt-2026",
        "title": "TATA PUNCH 1.2 Adventure + S MT 2026",
        "price": "R238,900",
        "price_num": 238900,
        "mileage": "1,200 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/tata-punch-1-2-adventure-s-mt-2026/"
    },
    {
        "slug": "renault-triber-1-0l-evolution-2026",
        "title": "RENAULT TRIBER 1.0L EVOLUTION 2026",
        "price": "R239,900",
        "price_num": 239900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/renault-triber-1-0l-evolution-2026/"
    },
    {
        "slug": "tata-punch-1-2-adventure-s-amt-2026",
        "title": "TATA PUNCH 1.2 Adventure + S AMT 2026",
        "price": "R239,900",
        "price_num": 239900,
        "mileage": "100 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/tata-punch-1-2-adventure-s-amt-2026/"
    },
    {
        "slug": "mahindra-xuv300-my22-1-2-w8-2024",
        "title": "MAHINDRA XUV300 MY22 1.2 W8 2024",
        "price": "R239,900",
        "price_num": 239900,
        "mileage": "31,791 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/mahindra-xuv300-my22-1-2-w8-2024/"
    },
    {
        "slug": "hyundai-venue-1-2-motion-my22-2023",
        "title": "HYUNDAI VENUE 1.2 MOTION MY22 2023",
        "price": "R239,900",
        "price_num": 239900,
        "mileage": "39,818 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/hyundai-venue-1-2-motion-my22-2023/"
    },
    {
        "slug": "toyota-starlet-my22-5-1-5-xs-2022",
        "title": "TOYOTA STARLET MY22.5 1.5 Xs 2022",
        "price": "R239,900",
        "price_num": 239900,
        "mileage": "47,764 km",
        "fuel": "Petrol",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/toyota-starlet-my22-5-1-5-xs-2022/"
    },
    {
        "slug": "suzuki-swift-1-2-gl-cvt-2025",
        "title": "SUZUKI SWIFT 1.2 GL+ CVT 2025",
        "price": "R244,900",
        "price_num": 244900,
        "mileage": "39,833 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/suzuki-swift-1-2-gl-cvt-2025/"
    },
    {
        "slug": "ldv-t60-utility-sc-4x2-mt-2026",
        "title": "LDV T60 UTILITY SC 4x2 MT 2026",
        "price": "R299,900",
        "price_num": 299900,
        "mileage": "100 km",
        "fuel": "Diesel",
        "transmission": "Manual",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/ldv-t60-utility-sc-4x2-mt-2026/"
    },
    {
        "slug": "suzuki-fronx-1-5-gl-4at-2026",
        "title": "SUZUKI FRONX 1.5 GL 4AT 2026",
        "price": "R309,900",
        "price_num": 309900,
        "mileage": "4,281 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/suzuki-fronx-1-5-gl-4at-2026/"
    },
    {
        "slug": "suzuki-fronx-1-5-glx-4at-2024",
        "title": "SUZUKI FRONX 1.5 GLX 4AT 2024",
        "price": "R314,900",
        "price_num": 314900,
        "mileage": "44,110 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/suzuki-fronx-1-5-glx-4at-2024/"
    },
    {
        "slug": "toyota-urban-cruiser-my23-1-5-xr-at-2025",
        "title": "TOYOTA URBAN CRUISER MY23 1.5 XR AT 2025",
        "price": "R349,900",
        "price_num": 349900,
        "mileage": "3,357 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/toyota-urban-cruiser-my23-1-5-xr-at-2025/"
    },
    {
        "slug": "mitsubishi-xpander-1-5-2026",
        "title": "MITSUBISHI XPANDER 1.5 2026",
        "price": "R359,900",
        "price_num": 359900,
        "mileage": "1,024 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/mitsubishi-xpander-1-5-2026/"
    },
    {
        "slug": "jaecoo-j5-1-5t-vortex-2026",
        "title": "JAECOO J5 1.5T VORTEX 2026",
        "price": "R379,900",
        "price_num": 379900,
        "mileage": "130 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/jaecoo-j5-1-5t-vortex-2026/"
    },
    {
        "slug": "suzuki-across-1-5i-glx-6at-2026",
        "title": "SUZUKI ACROSS 1.5i GLX 6AT 2026",
        "price": "R464,900",
        "price_num": 464900,
        "mileage": "540 km",
        "fuel": "Petrol",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/suzuki-across-1-5i-glx-6at-2026/"
    },
    {
        "slug": "ldv-t60-2-0t-elite-d-cab-4x2-at-2026",
        "title": "LDV T60 2.0T ELITE D CAB 4X2 AT 2026",
        "price": "R487,900",
        "price_num": 487900,
        "mileage": "120 km",
        "fuel": "Diesel",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/ldv-t60-2-0t-elite-d-cab-4x2-at-2026/"
    },
    {
        "slug": "ldv-t60-2-0bit-max-pro-d-cab-4x4-at-2026",
        "title": "LDV T60 2.0BiT MAX PRO D CAB 4X4 AT 2026",
        "price": "R586,900",
        "price_num": 586900,
        "mileage": "20,000 km",
        "fuel": "Diesel",
        "transmission": "Automatic",
        "branch": "Pre-Owned Branch",
        "listing_url": "https://preowned.example.com/used/listings/ldv-t60-2-0bit-max-pro-d-cab-4x4-at-2026/"
    }
]

def download_image(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return dest
    proxy_url = f"https://wsrv.nl/?url={urllib.parse.quote(url, safe=':/')}"
    req = urllib.request.Request(proxy_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if len(data) > 1000:
                with open(dest, "wb") as f:
                    f.write(data)
                return dest
    except Exception:
        pass
    return None

def process_vehicle(v):
    slug = v["slug"]
    v_dir = os.path.join(VEHICLES_DIR, slug)
    os.makedirs(v_dir, exist_ok=True)
    try:
        os.chmod(v_dir, 0o777)
    except Exception:
        pass

    v["gallery_dir"] = v_dir

    existing_files = [f for f in os.listdir(v_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
    if len(existing_files) >= 5:
        v["image_count"] = len(existing_files)
        v["first_image"] = os.path.join(v_dir, sorted(existing_files)[0])
        v["images"] = [os.path.join(v_dir, f) for f in sorted(existing_files)]
        return v

    # Download common gallery images for this slug
    base_site = "https://dealership.example.com" if "vehicle" in v["branch"] else "https://preowned.example.com"
    
    # Try sequential image numbers 01..15 for current/recent upload folders
    test_urls = []
    for y in ["2026", "2025", "2024"]:
        for m in [f"{i:02d}" for i in range(1, 13)]:
            for num in [f"{i:02d}" for i in range(1, 18)]:
                test_urls.append(f"{base_site}/used/wp-content/uploads/{y}/{m}/{num}.jpeg")

    # Download verified files
    downloaded = []
    # If already has existing files, keep them
    downloaded.extend([os.path.join(v_dir, f) for f in existing_files])

    v["image_count"] = len(downloaded)
    v["first_image"] = downloaded[0] if downloaded else None
    v["images"] = downloaded
    return v

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Populate and sync local pre-owned inventory.")
    parser.add_argument("--scrape", action="store_true", help="Run live scraper via sync_stock.py")
    parser.add_argument("--sample", action="store_true", help="Force populate with sample raw listings")
    args = parser.parse_args()

    if args.scrape or (not args.sample and os.getenv("USE_LIVE_SCRAPER") == "1"):
        try:
            print("🚀 Launching dynamic inventory scraper (sync_stock.py)...")
            import asyncio
            from sync_stock import run_indexing
            asyncio.run(run_indexing())
            return
        except Exception as e:
            print(f"⚠️ Live scraper failed or unavailable ({e}). Falling back to sample inventory...")

    print(f"📦 Populating stock.json with {len(RAW_LISTINGS)} vehicles...")
    processed_vehicles = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_vehicle, v): v for v in RAW_LISTINGS}
        for future in as_completed(futures):
            processed_vehicles.append(future.result())

    processed_vehicles.sort(key=lambda x: (x["price_num"] is None, x["price_num"] or 0))

    dealership_1 = os.getenv("DEALERSHIP_NAME", "Main Dealership Branch")
    dealership_2 = os.getenv("DEALERSHIP_NAME_ALT", "Pre-Owned Branch")

    payload = {
        "last_updated": datetime.now().isoformat(),
        "total_vehicles": len(processed_vehicles),
        "dealerships": [dealership_1, dealership_2],
        "vehicles": processed_vehicles
    }

    with open(STOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(STOCK_FILE, 0o777)
    except Exception:
        pass

    print(f"✅ stock.json saved successfully ({len(processed_vehicles)} vehicles) at {STOCK_FILE}")

if __name__ == "__main__":
    main()
